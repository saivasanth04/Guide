import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import json
import pytest
from routing import RotationStore, CompletionService, RoutingError

from tests.helpers import FakeRouter, deployments


def test_rechecking_denied_deployment_only_restores_that_deployment(tmp_path):
    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    store.failure(ds[0], 403, None, stop_all=False)
    service = CompletionService(ds, store, FakeRouter)
    assert service.probe_blocked()["routing_restored"] is True
    assert service.router.calls == [ds[0].id]
    assert store.select(ds) == ds[0]


def test_json_and_zero_retries(tmp_path):
    service = CompletionService(deployments(), RotationStore(tmp_path / "quota.db"), FakeRouter)
    assert service.structured_completion("hello")["answer"] == "actual answer"
    for content in ["not json", "{}", '{"answer":3}', '{"answer":" "}', '{"answer":"x","extra":1}']:
        service.router.content = content
        with pytest.raises(RoutingError) as error:
            service.structured_completion("hello")
        assert error.value.status_code == 502
    assert len(service.router.calls) == 6
    assert service.router.config["max_fallbacks"] == 0


def test_failure_cooldown_and_enforcement(tmp_path):
    ds = deployments()[:1]
    ledger = RotationStore(tmp_path / "quota.db", lambda: 1000)
    service = CompletionService(ds, ledger, FakeRouter)

    class ProviderError(Exception):
        status_code = 429
        headers = {"Retry-After": "120"}

    service.router.error = ProviderError("mock-secret")
    with pytest.raises(RoutingError) as e:
        service.structured_completion("hello")
    assert "mock-secret" not in json.dumps(e.value.as_dict())
    with pytest.raises(RoutingError) as e:
        service.structured_completion("hello")
    assert e.value.retry_after == 120
    assert len(service.router.calls) == 1
    ledger.failure(ds[0], 403, None)
    with pytest.raises(RoutingError) as e:
        RotationStore(ledger.path).select(deployments())
    assert e.value.status_code == 403


def test_transient_failure_uses_next_model_with_real_router(tmp_path, monkeypatch):
    import litellm

    calls = []

    def upstream(**kwargs):
        calls.append(kwargs["model"])
        if len(calls) <= 3:
            raise litellm.ServiceUnavailableError(
                message="high demand", model=kwargs["model"], llm_provider="gemini"
            )
        return litellm.ModelResponse(
            choices=[{"message": {"role": "assistant", "content": '{"answer":"Hello!"}'}}]
        )

    monkeypatch.setattr(litellm, "completion", upstream)
    ds = deployments()
    path = tmp_path / "quota.db"
    service = CompletionService(ds, RotationStore(path), sleeper=lambda _: None)
    result = service.structured_completion("hi")
    assert calls == ["gemini/" + ds[0].model] * 3 + ["gemini/" + ds[1].model]
    assert result["answer"] == "Hello!"
    assert result["diagnostics"]["selected_model"] == ds[1].model
    assert [a["status"] for a in result["diagnostics"]["attempts"]] == [503, 503, 503, 200]
    assert RotationStore(path).select(ds) == ds[2]


def test_transient_failover_is_bounded_and_optional(tmp_path):

    class Unavailable(Exception):
        status_code = 503

    for maximum in (1, 3):
        service = CompletionService(
            deployments(),
            RotationStore(tmp_path / f"{maximum}.db"),
            FakeRouter,
            max_attempts=maximum,
            sleeper=lambda _: None,
        )
        service.router.error = Unavailable("mock-secret")
        with pytest.raises(RoutingError) as error:
            service.structured_completion("hi")
        assert error.value.status_code == 503
        assert len(service.router.calls) == maximum * 3
        assert len(set(service.router.calls)) == maximum


def test_same_deployment_retry_succeeds_before_fallback(tmp_path):

    class Unavailable(Exception):
        status_code = 503

    class RecoveringRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Unavailable() if len(self.calls) < 2 else None
            return super().completion(**kwargs)

    sleeps = []
    ds = deployments()
    ledger = RotationStore(tmp_path / "quota.db")
    service = CompletionService(ds, ledger, RecoveringRouter, sleeper=sleeps.append)
    assert service.structured_completion("hi")["answer"] == "actual answer"
    assert service.router.calls == [ds[0].id] * 3
    assert 1 <= sleeps[0] <= 1.25
    assert 2 <= sleeps[1] <= 2.5
    assert ledger.select(ds) == ds[1]


def test_same_deployment_retries_can_be_disabled(tmp_path):

    class Unavailable(Exception):
        status_code = 503

    service = CompletionService(
        deployments(),
        RotationStore(tmp_path / "quota.db"),
        FakeRouter,
        max_attempts=1,
        retries_per_deployment=0,
    )
    service.router.error = Unavailable()
    with pytest.raises(RoutingError):
        service.structured_completion("hi")
    assert len(service.router.calls) == 1


def test_unavailable_model_fails_over_without_retry_and_persists(tmp_path):

    class Missing(Exception):
        status_code = 404

    class ModelAccessRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = (
                Missing("This model is no longer available to new users")
                if not self.calls
                else None
            )
            return super().completion(**kwargs)

    ds = deployments()
    path = tmp_path / "quota.db"
    sleeps = []
    service = CompletionService(ds, RotationStore(path), ModelAccessRouter, sleeper=sleeps.append)
    result = service.structured_completion("hi")
    assert service.router.calls == [ds[0].id, ds[1].id]
    assert [a["status"] for a in result["diagnostics"]["attempts"]] == [404, 200]
    assert not sleeps
    ledger = RotationStore(path)
    for _ in range(len(ds) * 2):
        assert ledger.select(ds) != ds[0]


def test_all_models_unavailable_returns_explicit_error(tmp_path):
    ds = deployments()
    ledger = RotationStore(tmp_path / "quota.db")
    for d in ds:
        ledger.mark_unavailable(d)
    with pytest.raises(RoutingError) as error:
        ledger.select(ds)
    assert error.value.status_code == 503
    assert len(error.value.diagnostics["unavailable_deployments"]) == len(ds)


def test_retry_after_prevents_early_same_model_retry(tmp_path):

    class Unavailable(Exception):
        status_code = 503
        headers = {"Retry-After": "120"}

    class RecoveringRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Unavailable() if not self.calls else None
            return super().completion(**kwargs)

    ds = deployments()
    ledger = RotationStore(tmp_path / "quota.db", lambda: 1000)
    service = CompletionService(ds, ledger, RecoveringRouter, sleeper=lambda _: None)
    assert service.structured_completion("hi")["answer"] == "actual answer"
    assert service.router.calls == [ds[0].id, ds[1].id]


def test_http_transient_retry_then_model_unavailable_then_success(tmp_path, monkeypatch, caplog):
    import httpx
    import logging
    from litellm.llms.custom_httpx.http_handler import HTTPHandler

    calls = []

    def post(self, url, headers=None, json=None, **kwargs):
        calls.append(url)
        if len(calls) <= 3:
            status, body = (
                503,
                {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}},
            )
        elif len(calls) == 4:
            status, body = (
                404,
                {
                    "error": {
                        "code": 404,
                        "message": "This model is no longer available to new users",
                        "status": "NOT_FOUND",
                    }
                },
            )
        else:
            status, body = (
                200,
                {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [{"text": '{"answer":"Hello!"}'}],
                            },
                            "finishReason": "STOP",
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 10,
                        "candidatesTokenCount": 5,
                        "totalTokenCount": 15,
                    },
                },
            )
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(HTTPHandler, "post", post)
    logger = logging.getLogger("LiteLLM Router")
    logger.addHandler(caplog.handler)
    try:
        service = CompletionService(
            deployments(), RotationStore(tmp_path / "quota.db"), sleeper=lambda _: None
        )
        response = service.structured_completion("hi")
    finally:
        logger.removeHandler(caplog.handler)
    assert response["answer"] == "Hello!"
    assert [a["status"] for a in response["diagnostics"]["attempts"]] == [503, 503, 503, 404, 200]
    assert len(calls) == 5
    assert not any(
        ("Error occurred while trying to do fallbacks" in r.getMessage() for r in caplog.records)
    )
