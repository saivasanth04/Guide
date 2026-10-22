"""Completion validation and explicit retry/fallback orchestration."""

import json
import random
import time
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
        retries_per_deployment=2,
        sleeper=time.sleep,
    ):
        max_attempts = len(deployments) if max_attempts is None else max_attempts
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts
        if type(retries_per_deployment) is not int or not 0 <= retries_per_deployment <= 3:
            raise ValueError("retries_per_deployment must be between 0 and 3")
        self.retries_per_deployment = retries_per_deployment
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
            timeout=60,
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

    def _run(self, messages, response_model=Answer):
        schema = response_schema(response_model)
        tried, attempts = set(), []
        for _ in range(min(self.max_attempts, len(self.deployments))):
            try:
                d = self.ledger.select(self.deployments, excluded=tried)
            except RoutingError as error:
                if attempts:
                    error.diagnostics["attempts"] = attempts
                raise
            tried.add(d.id)
            for retry_number in range(self.retries_per_deployment + 1):
                if retry_number:
                    delay = min(2 ** (retry_number - 1), 4)
                    self.sleeper(delay + random.uniform(0, delay * 0.25))
                    try:
                        # Retry the same key/model without advancing the rotation.
                        # Do not rewind the global cursor if another caller advanced it.
                        self.ledger.select(self.deployments, only=d.id)
                    except RoutingError as error:
                        if error.status_code != 429:
                            error.diagnostics["attempts"] = attempts
                            raise
                        # A provider Retry-After prevents retrying this deployment yet.
                        last_error = error
                        break
                try:
                    result = self._complete_once(messages, d, response_model, schema)
                except RoutingError as error:
                    failure = {
                        **d.diagnostics,
                        "status": error.status_code,
                        "attempt_number": len(attempts) + 1,
                        "retry_number": retry_number,
                        "error": str(error),
                    }
                    if "access_error" in error.diagnostics:
                        failure["reason"] = error.diagnostics["access_error"]["reason"]
                    attempts.append(failure)
                    if error.failover:
                        last_error = error
                        break
                    if not error.transient:
                        error.diagnostics["attempts"] = attempts
                        raise
                    last_error = error
                else:
                    result["diagnostics"]["failed_attempts"] = list(attempts)
                    result["diagnostics"]["attempt_count"] = len(attempts) + 1
                    result["diagnostics"]["attempts"] = [
                        *attempts,
                        {
                            **d.diagnostics,
                            "status": 200,
                            "attempt_number": len(attempts) + 1,
                            "retry_number": retry_number,
                        },
                    ]
                    return result
            if len(tried) >= min(self.max_attempts, len(self.deployments)):
                last_error.diagnostics["attempts"] = attempts
                raise last_error

    def _complete_once(self, messages, d, response_model=Answer, schema=None):
        schema = response_schema(response_model) if schema is None else schema
        token = self.strategy.selection.set(d)
        try:
            if d.response_mode == "json_object":
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
            if d.provider == "openrouter":
                # Keep upstream endpoint fallbacks disabled: application owns attempts.
                provider_options["extra_body"] = {
                    "provider": {"allow_fallbacks": False, "require_parameters": True}
                }
            response = self.router.completion(
                model="structured",
                messages=messages,
                response_format=response_format,
                num_retries=0,
                max_retries=0,
                fallbacks=[],
                **provider_options,
            )
            parsed = validate_response(response.choices[0].message.content, response_model, schema)
            payload = parsed.model_dump(mode="json")
            return {
                **(payload if response_model is Answer else {"data": payload}),
                "diagnostics": d.diagnostics,
            }
        except Exception as exc:
            status = getattr(exc, "status_code", None) or 502
            access = access_failure(exc, status)
            if access and status not in (401, 403):
                status = 403
            diagnostics = d.diagnostics
            if access:
                diagnostics["access_error"] = access
            headers = (
                getattr(exc, "headers", None)
                or getattr(getattr(exc, "response", None), "headers", {})
                or {}
            )
            retry = headers.get("retry-after") or headers.get("Retry-After")
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
                not isinstance(exc, RoutingError)
                and getattr(exc, "status_code", None) in (408, 500, 502, 503, 504)
                and status != 403
            )
            message = (
                access["explanation"]
                if access
                else "Model unavailable; deployment disabled locally"
                if unavailable
                else "Provider temporarily unavailable"
                if transient
                else "Upstream completion failed or returned invalid JSON"
            )
            raise RoutingError(
                status,
                message,
                diagnostics,
                retry,
                transient=transient,
                failover=unavailable or denied,
            ) from None
        finally:
            self.strategy.selection.reset(token)
