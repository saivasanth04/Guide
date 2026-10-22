"""Load provider deployments from local configuration, never embedded secrets."""

import json
import re
from urllib.parse import urlsplit

import litellm
from .schemas import Deployment


def load_provider_deployments(path, secrets):
    config = json.loads(path.read_text(encoding="utf-8"))
    if set(config) - {"include_legacy_gemini", "accounts"}:
        raise ValueError("Unknown LLM configuration fields")
    if type(config.get("include_legacy_gemini", False)) is not bool:
        raise ValueError("Invalid legacy setting")
    deployments, seen_accounts, seen_credentials = [], set(), set()
    accounts = config.get("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError("accounts must be a list")
    for account in accounts:
        if not isinstance(account, dict) or set(account) - {
            "enabled",
            "label",
            "provider",
            "api_key_env",
            "project_id",
            "quota_scope",
            "daily_reset",
            "reset_timezone",
            "tpm_accounting",
            "models",
            "api_base_env",
            "account_id",
        }:
            raise ValueError("Invalid account fields; use api_key_env, never api_key")
        if type(account.get("enabled", True)) is not bool:
            raise ValueError("Invalid enabled setting")
        if not account.get("enabled", True):
            continue
        provider, label = account["provider"], account["label"]
        if (
            provider not in litellm.provider_list
            or not isinstance(label, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", label)
        ):
            raise ValueError("Invalid provider or label")
        if label in seen_accounts:
            raise ValueError("Account labels must be unique")
        seen_accounts.add(label)
        key_ref = account["api_key_env"]
        if not isinstance(key_ref, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_ref):
            raise ValueError("Invalid credential environment reference")
        key = secrets.get(key_ref)
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Missing local provider credential")
        credential_id = (provider, key)
        if credential_id in seen_credentials:
            raise ValueError("Repeated provider credentials must use one account entry")
        seen_credentials.add(credential_id)
        api_base = None
        if account.get("api_base_env"):
            base_ref = account["api_base_env"]
            if not isinstance(base_ref, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", base_ref):
                raise ValueError("Invalid endpoint reference")
            api_base = secrets.get(base_ref)
            parsed = urlsplit(api_base or "")
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "Use an endpoint URL without embedded credentials or query strings"
                )
        project = account.get("project_id") or None
        scope = account.get("account_id") or account.get("quota_scope") or project or label
        if project is not None and (not isinstance(project, str) or not project.strip()):
            raise ValueError("Invalid project ID")
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("Invalid account identifier")
        models = account["models"]
        if not isinstance(models, list) or not models:
            raise ValueError("Configure at least one model per account")
        for model in models:
            if not isinstance(model, dict) or set(model) - {
                "model",
                "limits",
                "shared_groups",
                "input_budget",
                "output_budget",
                "response_mode",
                "input_overhead",
            }:
                raise ValueError("Invalid model fields")
            name = model["model"]
            mode = model.get("response_mode", "json_schema")
            if (
                not isinstance(name, str)
                or not name.strip()
                or name.startswith(provider + "/")
                or mode not in ("json_schema", "json_object")
            ):
                raise ValueError("Use provider-relative model names and a supported JSON mode")
            # Former quota/token-budget fields are accepted but never enforced.
            deployment = Deployment(
                label,
                project,
                name,
                key,
                provider=provider,
                account_id=scope,
                response_mode=mode,
                api_base=api_base,
                account_label=label,
            )
            deployments.append(deployment)
    return deployments, config.get("include_legacy_gemini", False)
