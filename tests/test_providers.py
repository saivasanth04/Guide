import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import json
import pytest
from routing import Deployment, RotationStore, CompletionService, RoutingError, MODELS

from tests.helpers import FakeRouter, deployments, provider_account, provider_env


def test_provider_429_without_retry_after_does_not_invent_a_local_cooldown(tmp_path):
    class RateLimited(Exception):
        status_code = 429

    ds = deployments()
    store = RotationStore(tmp_path / "state.db", lambda: 1000)
    service = CompletionService(ds, store, FakeRouter)
    service.router.error = RateLimited()
    with pytest.raises(RoutingError) as error:
        service.structured_completion("hi")
    assert error.value.status_code == 429
    assert store.select(ds, only=ds[0].id) == ds[0]


def test_new_provider_config_needs_no_quota_settings(tmp_path):
    from routing import load_service

    account = {
        "label": "groq-account",
        "provider": "groq",
        "api_key_env": "GROQ_API_KEY",
        "models": [{"model": "mock-model", "response_mode": "json_object"}],
    }
    service = load_service(provider_env(tmp_path, [account]), FakeRouter)
    assert service.structured_completion("hi")["answer"] == "actual answer"


def test_real_router_mocked_transport(tmp_path, monkeypatch):
    import litellm

    calls = []

    def upstream(**kwargs):
        calls.append(kwargs)
        raise litellm.RateLimitError(
            message="mock failure", model="gemini/mock", llm_provider="gemini"
        )

    monkeypatch.setattr(litellm, "completion", upstream)
    service = CompletionService(deployments(), RotationStore(tmp_path / "quota.db"))
    with pytest.raises(RoutingError):
        service.structured_completion("hello")
    assert len(calls) == 1


def test_non_model_404_does_not_route_around_configuration_error(tmp_path):

    class Missing(Exception):
        status_code = 404

    service = CompletionService(deployments(), RotationStore(tmp_path / "quota.db"), FakeRouter)
    service.router.error = Missing("Endpoint resource missing")
    with pytest.raises(RoutingError):
        service.structured_completion("hi")
    assert len(service.router.calls) == 1


@pytest.mark.parametrize("model", list(MODELS))
def test_new_models_through_gemini_http_adapter(tmp_path, monkeypatch, model):
    import httpx
    from litellm.llms.custom_httpx.http_handler import HTTPHandler

    requests = []

    def post(self, url, headers=None, json=None, **kwargs):
        requests.append((url, json))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": '{"answer":"Hello!"}'}]},
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 15,
                },
                "modelVersion": model,
            },
        )

    monkeypatch.setattr(HTTPHandler, "post", post)
    ds = [Deployment("test-key", "test-project", model, "mock-secret")]
    service = CompletionService(ds, RotationStore(tmp_path / "quota.db"))
    assert service.structured_completion("hi")["answer"] == "Hello!"
    assert len(requests) == 1
    assert model in requests[0][0]
    assert requests[0][1]["generationConfig"]["response_mime_type"] == "application/json"


def test_explicit_missing_model_file_fails_configuration(tmp_path):
    from routing import load_service

    env = tmp_path / ".env"
    env.write_text(
        'GEMINI_PROJECTS=\'[{"label":"test","project_id":"p","api_key":"mock-secret"}]\'\nMODELS_FILE=missing.json\n'
    )
    with pytest.raises(RoutingError) as error:
        load_service(env, FakeRouter)
    assert error.value.status_code == 503


def test_multi_provider_configuration_and_normalized_response(tmp_path, monkeypatch):
    import chat_client
    from routing import load_service

    accounts = [
        provider_account("groq", "openai/gpt-oss-20b"),
        provider_account("openrouter", "vendor/test-model"),
        provider_account("gemini", "gemini-3.8-flash"),
    ]
    service = load_service(provider_env(tmp_path, accounts), FakeRouter)
    monkeypatch.setattr(chat_client, "_service", service)
    from llm import LLMClient

    client = LLMClient()
    for provider in ("groq", "openrouter", "gemini"):
        response = client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
        assert response.choices[0].message.content == "actual answer"
        assert response.diagnostics["provider"] == provider
    assert [d["litellm_params"]["model"] for d in service.router.model_list] == [
        "groq/openai/gpt-oss-20b",
        "openrouter/vendor/test-model",
        "gemini/gemini-3.8-flash",
    ]


@pytest.mark.parametrize(
    "provider,model", [("groq", "openai/gpt-oss-20b"), ("openrouter", "vendor/test-model")]
)
def test_generic_provider_http_adapters(tmp_path, monkeypatch, provider, model):
    import httpx
    import json as json_module
    from routing import load_service
    from litellm.llms.custom_httpx.http_handler import HTTPHandler

    calls = []

    def respond(request, body):
        calls.append((str(request.url), body))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "mock-id",
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "service_tier": "on_demand",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"answer":"Hello!"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            },
        )

    def post(self, url, headers=None, json=None, data=None, **kwargs):
        return respond(
            httpx.Request("POST", url), json if json is not None else json_module.loads(data)
        )

    def send(self, request, **kwargs):
        return respond(request, json_module.loads(request.content))

    monkeypatch.setattr(HTTPHandler, "post", post)
    monkeypatch.setattr(httpx.Client, "send", send)
    service = load_service(provider_env(tmp_path, [provider_account(provider, model)]))
    service.sleeper = lambda _: None
    result = service.structured_completion("hi")
    assert result["answer"] == "Hello!"
    assert result["diagnostics"]["provider"] == provider
    assert len(calls) == 1
    assert calls[0][1]["response_format"] == {"type": "json_object"}
    assert "JSON" in calls[0][1]["messages"][0]["content"]
    if provider == "openrouter":
        assert calls[0][1]["provider"] == {"allow_fallbacks": False, "require_parameters": True}


def test_cross_provider_fallback_and_enforcement_stop(tmp_path):
    from routing import load_service

    class Temporary(Exception):
        status_code = 503

    class RecoveringRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Temporary() if len(self.calls) < 3 else None
            return super().completion(**kwargs)

    service = load_service(
        provider_env(tmp_path, [provider_account("groq"), provider_account("openrouter")]),
        RecoveringRouter,
    )
    service.sleeper = lambda _: None
    result = service.structured_completion("hi")
    assert result["diagnostics"]["provider"] == "openrouter"
    assert [a["provider"] for a in result["diagnostics"]["attempts"]] == ["groq"] * 3 + [
        "openrouter"
    ]
    service.ledger.failure(service.deployments[0], 403, None)
    with pytest.raises(RoutingError) as error:
        service.structured_completion("hi")
    assert error.value.status_code == 403
    assert len(service.router.calls) == 4


def test_provider_json_modes_and_validation(tmp_path):
    from routing import load_service

    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.kwargs = kwargs
            return super().completion(**kwargs)

    account = provider_account("groq")
    account["models"][0]["response_mode"] = "json_schema"
    service = load_service(provider_env(tmp_path, [account]), RecordingRouter)
    service.structured_completion("hi")
    assert service.router.kwargs["response_format"]["type"] == "json_schema"
    service.router.content = '{"answer":false}'
    with pytest.raises(RoutingError) as error:
        service.structured_completion("hi")
    assert error.value.status_code == 502
    assert error.value.diagnostics["provider"] == "groq"
    assert len(service.router.calls) == 2


def test_custom_endpoint_reference_stays_outside_application_code(tmp_path):
    from routing import load_service

    account = provider_account("openai", "custom-model", api_base_env="CUSTOM_API_BASE")
    env = provider_env(tmp_path, [account])
    with env.open("a") as file:
        file.write("CUSTOM_API_BASE=https://example.test/v1\n")
    service = load_service(env, FakeRouter)
    assert service.router.model_list[0]["litellm_params"]["api_base"] == "https://example.test/v1"
    assert service.structured_completion("hi")["diagnostics"]["provider"] == "openai"
    assert "api_base" not in service.deployments[0].diagnostics


def test_legacy_gemini_can_be_combined_without_changing_its_ids(tmp_path):
    from routing import load_service

    account = provider_account("groq")
    env = provider_env(tmp_path, [account])
    (tmp_path / "llm.json").write_text(
        json.dumps({"include_legacy_gemini": True, "accounts": [account]})
    )
    with env.open("a") as file:
        file.write(
            'GEMINI_PROJECTS=\'[{"label":"legacy","project_id":"legacy-project","api_key":"mock-legacy-secret"}]\'\n'
        )
    service = load_service(env, FakeRouter)
    assert service.deployments[0].provider == "groq"
    assert len(service.deployments) == 1 + len(MODELS)
    for deployment in service.deployments[1:]:
        assert deployment.id == json.dumps(
            ["legacy-project", deployment.model], separators=(",", ":")
        )


def test_real_router_startup_and_success_without_cost_warnings(tmp_path, monkeypatch, caplog):
    import litellm

    calls = []

    def upstream(**kwargs):
        calls.append(kwargs)
        return litellm.ModelResponse(
            model=kwargs["model"],
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": '{"answer":"Hello!"}'},
                }
            ],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    monkeypatch.setattr(litellm, "completion", upstream)
    ds = deployments()
    for d in ds:
        d.project_id = "startup-" + d.project_id
    expected_prices = [CompletionService.model_info(d) for d in ds]
    logger = litellm.utils.verbose_logger
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("WARNING", logger="LiteLLM"):
            service = CompletionService(ds, RotationStore(tmp_path / "quota.db"))
            result = service.structured_completion("hi")
    finally:
        logger.removeHandler(caplog.handler)
    assert result["answer"] == "Hello!"
    assert len(calls) == 1
    assert not any(("register_model: model=" in r.message for r in caplog.records))
    for d, expected in zip(service.router.model_list, expected_prices):
        if "cache_read_input_token_cost" in expected:
            assert (
                d["model_info"]["cache_read_input_token_cost"]
                == expected["cache_read_input_token_cost"]
            )
