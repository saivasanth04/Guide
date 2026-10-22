"""Shared fake providers and local configuration builders."""

import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from types import SimpleNamespace as NS
import json
from routing import Deployment, MODELS


def deployments(rpm=100):
    return [
        Deployment(f"key_{i}", f"project{i}", model, "mock-secret")
        for i in range(2)
        for model in MODELS
    ]


class FakeRouter:
    def __init__(self, **kwargs):
        self.config = kwargs
        self.model_list = kwargs["model_list"]
        self.calls = []
        self.content = '{"answer":"actual answer"}'
        self.error = None

    def set_custom_routing_strategy(self, strategy):
        self.strategy = strategy

    def completion(self, **kwargs):
        selected = self.strategy.get_available_deployment(**kwargs)
        self.calls.append(selected["model_info"]["id"])
        assert kwargs["num_retries"] == kwargs["max_retries"] == 0
        if self.error:
            raise self.error
        return NS(choices=[NS(message=NS(content=self.content))])


def provider_account(provider, model="test-model", **overrides):
    account = {
        "label": provider + "-account",
        "provider": provider,
        "api_key_env": provider.upper() + "_API_KEY",
        "quota_scope": "test-scope",
        "models": [
            {
                "model": model,
                "response_mode": "json_object",
                "limits": {"rpm": 100, "tpm": 1000000, "rpd": 1000},
            }
        ],
    }
    account.update(overrides)
    return account


def provider_env(tmp_path, accounts):
    (tmp_path / "llm.json").write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "LLM_CONFIG=llm.json\nQUOTA_DB=quota.db\n"
        + "".join((f"{a['api_key_env']}=mock-{i}-secret\n" for i, a in enumerate(accounts))),
        encoding="utf-8",
    )
    return env
