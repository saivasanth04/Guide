"""Completion validation and explicit retry/fallback orchestration."""

import json
import contextvars
import math
import queue
import threading
import time
import httpx
from .runtime import _CACHE_PRICES
from litellm import Router
from .defaults import ENFORCEMENT_REASONS
from .errors import RoutingError
from .schemas import Answer, response_schema, validate_response
from .strategy import PersistentRoundRobin
from .provider_errors import access_failure


class CompletionService:
    def blocked_diagnostics(self):
        with self.ledger.connect() as db:
            row = db.execute("SELECT v FROM state WHERE k='blocked'").fetchone()
            if not row:
                row = db.execute(
                    "SELECT diagnostics FROM access_denials ORDER BY detected DESC LIMIT 1"
                ).fetchone()
        if not row:
            return None
        diagnostics = json.loads(row[0])
        diagnostics.setdefault(
            "access_error",
            {
                "reason": "PERMISSION_DENIED",
                "explanation": "Original provider reason was discarded by the previous implementation.",
                "action": "Inspect the credential in the provider console or explicitly probe this same blocked deployment.",
            },
        )
        return diagnostics

    def probe_blocked(self):
        """Explicit one-attempt access recheck of the blocked key/model only."""
        with self.ledger.connect() as db:
            snapshot = db.execute("SELECT v FROM state WHERE k='blocked'").fetchone()
            global_stop = snapshot is not None
            if not snapshot:
                snapshot = db.execute(
                    "SELECT diagnostics FROM access_denials ORDER BY detected DESC LIMIT 1"
                ).fetchone()
        if not snapshot:
            raise RoutingError(400, "There is no blocked deployment to diagnose")
        blocked = json.loads(snapshot[0])
        deployment = next(
            (
                d
                for d in self.deployments
                if all(
                    blocked.get(k) == d.diagnostics.get(k)
                    for k in ("provider", "key_label", "project_id", "selected_model")
                )
            ),
            None,
        )
        if deployment is None:
            raise RoutingError(400, "The blocked deployment is no longer configured")
        with self.ledger.connect() as db:
            cooldown = db.execute(
                "SELECT until FROM cooldown WHERE bucket=?", (deployment.id,)
            ).fetchone()
        if cooldown and cooldown[0] > self.ledger.clock():
            raise RoutingError(
                429,
                "Provider Retry-After is still active",
                deployment.diagnostics,
                cooldown[0] - self.ledger.clock(),
            )
        result = self._complete_once([{"role": "user", "content": "hi"}], deployment)
        # A successful recheck demonstrates restored access. Never clear a newer stop.
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = (
                db.execute("SELECT v FROM state WHERE k='blocked'").fetchone()
                if global_stop
                else db.execute(
                    "SELECT diagnostics FROM access_denials WHERE deployment=?", (deployment.id,)
                ).fetchone()
            )
            cleared = current == snapshot
            if cleared:
                if global_stop:
                    db.execute("DELETE FROM state WHERE k='blocked'")
                else:
                    db.execute("DELETE FROM access_denials WHERE deployment=?", (deployment.id,))
                    db.execute(
                        "DELETE FROM unavailable WHERE deployment=? AND reason=?",
                        (deployment.id, "provider_access_denied"),
                    )
            db.commit()
        return {**result, "routing_restored": cleared}

    @staticmethod
    def model_info(deployment):
        backend = deployment.backend
        catalog = _CACHE_PRICES.get(backend, {})
        # LiteLLM registers custom deployment IDs as cost-map aliases. Attach
        # real bundled cache prices so it does not treat each alias as unknown.
        # Do not invent zero prices or override input/output pricing.
        return {"id": deployment.id, **catalog}

    def __init__(
        self,
        deployments,
        ledger,
        router_factory=Router,
        max_attempts=None,
        retries_per_deployment=0,
        sleeper=time.sleep,
        request_timeout=30,
        routing_time_budget=60,
        clock=time.monotonic,
    ):
        max_attempts = len(deployments) if max_attempts is None else max_attempts
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        if type(retries_per_deployment) is not int or not 0 <= retries_per_deployment <= 3:
            raise ValueError("retries_per_deployment must be between 0 and 3")
        # Legacy settings remain accepted; provider failures now always fail over.
        self.retries_per_deployment = retries_per_deployment
        for name, value in (
            ("request_timeout", request_timeout),
            ("routing_time_budget", routing_time_budget),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite number")
        self.request_timeout = request_timeout
        self.routing_time_budget = routing_time_budget
        self.clock = clock
        self.sleeper = sleeper
        self.deployments, self.ledger = deployments, ledger
        self.strategy = PersistentRoundRobin(ledger, deployments)
        self.router = router_factory(
            model_list=[
                {
                    "model_name": "structured",
                    "model_info": self.model_info(d),
                    "litellm_params": {
                        "model": d.backend,
                        "api_key": d.api_key,
                        **({"api_base": d.api_base} if d.api_base else {}),
                        "num_retries": 0,
                        "max_retries": 0,
                    },
                }
                for d in deployments
            ],
            num_retries=0,
            max_fallbacks=0,
            fallbacks=[],
            default_fallbacks=[],
            context_window_fallbacks=[],
            content_policy_fallbacks=[],
            disable_cooldowns=True,
            cache_responses=False,
            timeout=request_timeout,
        )
        self.strategy.model_list = self.router.model_list
        self.router.set_custom_routing_strategy(self.strategy)

    def structured_completion(self, prompt, response_model=Answer):
        if not isinstance(prompt, str) or not prompt.strip():
            raise RoutingError(400, "prompt must be a nonempty string")
        return self._run([{"role": "user", "content": prompt}], response_model)

    def chat_completion(self, messages, response_model=Answer):
        if not isinstance(messages, list) or not messages:
            raise RoutingError(400, "messages must be a nonempty list")
        normalized = []
        for message in messages:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] not in ("system", "user", "assistant")
                or not isinstance(message["content"], str)
                or not message["content"].strip()
            ):
                raise RoutingError(400, "Use text messages with system, user, or assistant roles")
            normalized.append({"role": message["role"], "content": message["content"]})
        return self._run(normalized, response_model)

    def generate_content(self, *, model, messages, response_model=None, generation_options=None):
        requested = model.removeprefix("models/")
        deployments = (
            self.deployments
            if requested in ("router", "structured")
            else [d for d in self.deployments if requested in (d.model, d.backend)]
        )
        if not deployments:
            raise RoutingError(400, "Requested model is not in local routing configuration")
        return self._run(messages, response_model, deployments, generation_options)

    def _run(self, messages, response_model=Answer, deployments=None, generation_options=None):
        deadline = self.clock() + self.routing_time_budget
        schema = response_schema(response_model) if response_model is not None else None
        deployments = self.deployments if deployments is None else deployments
        tried, attempts = set(), []
        last_failure = None
        request_messages = messages

        def remaining():
            seconds = deadline - self.clock()
            if seconds <= 0:
                raise RoutingError(
                    504,
                    "LLM routing time budget exhausted",
                    {
                        "time_budget_exhausted": True,
                        "routing_time_budget_seconds": self.routing_time_budget,
                        "attempt_count": len(attempts),
                        "attempts": list(attempts),
                    },
                )
            return seconds

        for _ in range(min(self.max_attempts, len(deployments))):
            remaining()
            try:
                d = self.ledger.select(deployments, excluded=tried)
            except RoutingError as error:
                if attempts:
                    if last_failure is not None and error.status_code == 503:
                        # A skipped/disabled slot can remain after every usable
                        # deployment was tried. Keep the actual provider failure
                        # authoritative instead of disguising 429/503/validation
                        # failures as a new pool-selection/access problem.
                        last_failure.diagnostics["selection_error"] = error.as_dict()
                        last_failure.diagnostics["attempts"] = list(attempts)
                        raise last_failure from None
                    error.diagnostics["attempts"] = attempts
                raise
            tried.add(d.id)
            try:
                result = self._complete_once(
                    request_messages,
                    d,
                    response_model,
                    schema,
                    generation_options,
                    timeout=min(self.request_timeout, remaining()),
                )
            except RoutingError as error:
                last_failure = error
                failure = {
                    **d.diagnostics,
                    "status": error.status_code,
                    "attempt_number": len(attempts) + 1,
                    "retry_number": 0,
                    "error": str(error),
                }
                if "access_error" in error.diagnostics:
                    failure["reason"] = error.diagnostics["access_error"]["reason"]
                for field in ("failure_stage", "exception_type", "validation_errors"):
                    if field in error.diagnostics:
                        failure[field] = error.diagnostics[field]
                attempts.append(failure)
                error.diagnostics["attempts"] = list(attempts)
                # Access enforcement remains terminal even at the deadline.
                if not error.failover:
                    raise
                if error.diagnostics.get("failure_stage") == "response_validation":
                    request_messages = [*messages, {"role": "system", "content":
                        "The previous response failed validation. Return only the requested response "
                        "matching its format and supplied schema (if any), with required fields and types. Validation: "
                        + json.dumps(error.diagnostics.get("validation_errors", []))}]
                remaining()
                if len(tried) >= min(self.max_attempts, len(deployments)):
                    raise
            else:
                remaining()
                result["diagnostics"]["failed_attempts"] = list(attempts)
                result["diagnostics"]["attempt_count"] = len(attempts) + 1
                result["diagnostics"]["attempts"] = [
                    *attempts,
                    {
                        **d.diagnostics,
                        "status": 200,
                        "attempt_number": len(attempts) + 1,
                        "retry_number": 0,
                    },
                ]
                return result

    def _complete_once(
        self,
        messages,
        d,
        response_model=Answer,
        schema=None,
        generation_options=None,
        timeout=None,
    ):
        schema = (
            response_schema(response_model)
            if schema is None and response_model is not None
            else schema
        )
        token = self.strategy.selection.set(d)
        stage = "request_configuration"
        response_format = None
        try:
            options = dict(generation_options or {})
            if response_model is None:
                response_format = options.pop("response_format", None)
            elif d.response_mode == "json_object":
                response_format = {"type": "json_object"}
                messages = [
                    {
                        "role": "system",
                        "content": "Return only a JSON object matching this JSON schema: "
                        + json.dumps(schema),
                    },
                    *messages,
                ]
            else:
                response_format = {
                    "type": "json_schema",
                    "json_schema": {"name": "response", "strict": True, "schema": schema},
                }
            provider_options = {}
            if d.provider == "gemini":
                # This facade accepts text/JSON, never native tool-call responses.
                # ReAct tool descriptions in a text prompt can otherwise elicit
                # function calls with content=None from Gemini.
                provider_options["tool_choice"] = "none"
                if "reasoning_effort" in options:
                    # New Gemini models can be absent from LiteLLM's offline
                    # capability catalog. Use its actual Gemini translator and
                    # native request body rather than dropping the requested effort.
                    from litellm.llms.gemini.chat.transformation import GoogleAIStudioGeminiConfig

                    thinking = GoogleAIStudioGeminiConfig().map_openai_params(
                        {"reasoning_effort": options.pop("reasoning_effort")}, {},
                        model=d.model, drop_params=False,
                    )["thinkingConfig"]
                    provider_options["extra_body"] = {"generationConfig": {"thinkingConfig": thinking}}
            if d.provider == "openrouter":
                # Keep upstream endpoint fallbacks disabled: application owns attempts.
                provider_options["extra_body"] = {
                    "provider": {"allow_fallbacks": False, "require_parameters": True}
                }
            stage = "provider_request"
            response = self._provider_completion(
                timeout=self.request_timeout if timeout is None else timeout,
                model="structured",
                messages=messages,
                **({"response_format": response_format} if response_format is not None else {}),
                num_retries=0,
                max_retries=0,
                fallbacks=[],
                **options,
                **provider_options,
            )
            stage = "response_validation"
            if response_model is None:
                text = response.choices[0].message.content
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Provider returned no text")
                if response_format is not None:
                    json.loads(text)
                return {"answer": text, "diagnostics": d.diagnostics}
            parsed = validate_response(response.choices[0].message.content, response_model, schema)
            payload = parsed.model_dump(mode="json")
            return {
                **(payload if response_model is Answer else {"data": payload}),
                "diagnostics": d.diagnostics,
            }
        except Exception as exc:
            timed_out = isinstance(exc, (TimeoutError, httpx.TimeoutException))
            status = 408 if timed_out else getattr(exc, "status_code", None) or 502
            access = access_failure(exc, status)
            if access and status not in (401, 403):
                status = 403
            diagnostics = d.diagnostics
            diagnostics["failure_stage"] = stage
            diagnostics["exception_type"] = type(exc).__name__
            if stage == "response_validation" and hasattr(exc, "validator"):
                def leaves(error):
                    children = getattr(error, "context", [])
                    return [leaf for child in children for leaf in leaves(child)] if children else [error]
                errors = sorted(leaves(exc), key=lambda item: len(item.absolute_path), reverse=True)
                # Schema locations/validator names only: never echo provider
                # response values or exception messages containing user data.
                diagnostics["validation_errors"] = [
                    {"path": list(item.absolute_path), "keyword": item.validator}
                    for item in errors[:5]
                ]
            if access:
                diagnostics["access_error"] = access
            headers = (
                getattr(exc, "headers", None)
                or getattr(getattr(exc, "response", None), "headers", {})
                or {}
            )
            retry = headers.get("retry-after") or headers.get("Retry-After")
            if status == 429 and retry is None:
                retry = 30
            enforcement = access is not None and access["reason"] in ENFORCEMENT_REASONS
            denied = status in (401, 403) and not enforcement
            self.ledger.failure(d, status, retry, diagnostics=diagnostics, stop_all=enforcement)
            provider_message = str(exc).lower()
            unavailable = (
                status == 404
                and "model" in provider_message
                and any(
                    reason in provider_message
                    for reason in (
                        "not found",
                        "not_found",
                        "no longer available",
                        "not available",
                        "not supported",
                    )
                )
            )
            if unavailable:
                self.ledger.mark_unavailable(d)
            # Never copy provider exception messages, request bodies, or credentials.
            transient = (
                stage == "provider_request"
                and not isinstance(exc, RoutingError)
                and (
                    status in (408, 429, 500, 502, 503, 504)
                    or isinstance(exc, (ConnectionError, httpx.TransportError))
                )
                and status != 403
            )
            message = (
                access["explanation"]
                if access
                else "Model unavailable; deployment disabled locally"
                if unavailable
                else "Provider temporarily unavailable"
                if transient
                else "Provider request failed"
                if diagnostics["failure_stage"] == "provider_request"
                else "Provider returned empty or invalid text"
                if response_model is None and response_format is None
                else "Provider returned an invalid structured response"
            )
            raise RoutingError(
                status,
                message,
                diagnostics,
                retry,
                transient=transient,
                failover=not enforcement
                and (unavailable or denied or transient or stage == "response_validation"
                     or type(exc).__name__ == "UnsupportedParamsError"),
            ) from None
        finally:
            self.strategy.selection.reset(token)

    def _provider_completion(self, *, timeout, **kwargs):
        """Bound caller wait even when a sync SDK ignores its transport timeout.

        A daemon worker only performs the provider call; late results cannot
        update the ledger or request diagnostics. Preserve the selected deployment
        ContextVar across the thread boundary. Do not wait for workers on shutdown.
        """
        outcome = queue.Queue(maxsize=1)
        context = contextvars.copy_context()

        def invoke():
            try:
                outcome.put((True, self.router.completion(timeout=timeout, **kwargs)))
            except Exception as error:
                outcome.put((False, error))

        threading.Thread(target=context.run, args=(invoke,), daemon=True).start()
        try:
            success, value = outcome.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Provider attempt time limit exceeded") from None
        if not success:
            raise value
        return value
