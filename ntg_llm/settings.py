"""Build the service from local secrets and non-secret configuration."""

import json
import math
from pathlib import Path
from . import runtime  # noqa: F401 - configure offline LiteLLM before importing Router
from litellm import Router
from .defaults import MODELS
from .errors import RoutingError
from .schemas import Deployment
from .store import RotationStore
from .service import CompletionService


def load_service(env_path=".env", router_factory=Router):
    from dotenv import dotenv_values

    env_path = Path(env_path).resolve()
    config = dotenv_values(env_path)

    def local_path(value):
        path = Path(value)
        return path if path.is_absolute() else env_path.parent / path

    try:
        configured_maximum = config.get(
            "COMPLETION_MAX_DEPLOYMENTS", config.get("COMPLETION_MAX_ATTEMPTS", "all")
        )
        max_attempts = None if configured_maximum == "all" else int(configured_maximum)
        retries = int(config.get("RETRIES_PER_DEPLOYMENT", "0"))
        request_timeout = float(config.get("PROVIDER_REQUEST_TIMEOUT", "30"))
        routing_time_budget = float(config.get("COMPLETION_TIME_BUDGET", "60"))
        if any(
            not math.isfinite(value) or value <= 0
            for value in (request_timeout, routing_time_budget)
        ):
            raise ValueError()
        if (max_attempts is not None and max_attempts < 1) or not 0 <= retries <= 3:
            raise ValueError()
        deployments = []
        include_legacy = True
        if config.get("LLM_CONFIG"):
            from .configuration import load_provider_deployments

            deployments, include_legacy = load_provider_deployments(
                local_path(config["LLM_CONFIG"]), config
            )
        if include_legacy:
            projects = json.loads(config["GEMINI_PROJECTS"])
            model_path = (
                local_path(config["MODELS_FILE"])
                if config.get("MODELS_FILE")
                else env_path.parent / "models.json"
            )
            if config.get("MODELS_FILE") and not model_path.exists():
                raise ValueError()
            model_records = (
                json.loads(model_path.read_text(encoding="utf-8"))
                if model_path.exists()
                else [{"model": name} for name in MODELS]
            )
            if not isinstance(model_records, list) or not model_records:
                raise ValueError()
            model_names = [r["model"] for r in model_records]
            if len(set(model_names)) != len(model_names) or any(
                not isinstance(m, str) or not m.startswith("gemini-") or "/" in m
                for m in model_names
            ):
                raise ValueError()
            seen = set()
            seen_keys = set()
            for index, p in enumerate(projects, 1):
                project = p.get("project_id") or None
                if (
                    any(not isinstance(p[k], str) or not p[k].strip() for k in ("label", "api_key"))
                    or (
                        project is not None
                        and (not isinstance(project, str) or not project.strip() or project in seen)
                    )
                    or p["api_key"] in seen_keys
                ):
                    raise ValueError()
                seen.add(project)
                seen_keys.add(p["api_key"])
                for model_config in model_records:
                    model = model_config["model"]
                    mode = model_config.get("response_mode", "json_schema")
                    if mode not in ("json_schema", "json_object"):
                        raise ValueError()
                    label = f"key_{index:03d}"
                    deployments.append(
                        Deployment(
                            label,
                            project,
                            model,
                            p["api_key"],
                            response_mode=mode,
                            account_label=p["label"],
                        )
                    )
        if not deployments:
            raise ValueError()
        if len({d.id for d in deployments}) != len(deployments):
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError, OSError):
        raise RoutingError(
            503,
            "Invalid local configuration: check LLM_CONFIG, credential references, and model settings",
        ) from None
    store = RotationStore(
        local_path(config.get("ROUTER_DB", config.get("QUOTA_DB", "router.sqlite3")))
    )
    store.migrate_permission_stop(deployments)
    return CompletionService(
        deployments,
        store,
        router_factory,
        max_attempts=max_attempts,
        retries_per_deployment=retries,
        request_timeout=request_timeout,
        routing_time_budget=routing_time_budget,
    )
