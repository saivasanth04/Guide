"""Budget, failover, and persistent routing regression coverage."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from ntg_llm.errors import RoutingError
from ntg_llm.schemas import Deployment
from ntg_llm.service import CompletionService
from ntg_llm.settings import load_service
from ntg_llm.store import RotationStore


def response(text='{"answer":"ok"}'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class ProviderError(Exception):
    def __init__(self, status, headers=None, message="provider failure"):
        super().__init__(message)
        self.status_code = status
        self.headers = headers or {}


class ControlledRouter:
    def __init__(self, **kwargs):
        self.config = kwargs
        self.model_list = kwargs["model_list"]
        self.calls = []
        self.behavior = lambda deployment, options: response()

    def set_custom_routing_strategy(self, strategy):
        self.strategy = strategy

    def completion(self, **kwargs):
        deployment = self.strategy.get_available_deployment("structured")
        self.calls.append((deployment["model_info"]["id"], kwargs))
        return self.behavior(deployment, kwargs)


def service(tmp_path, count=4, **kwargs):
    deployments = [Deployment(f"key_{i}", None, f"model-{i}", "secret") for i in range(count)]
    return CompletionService(
        deployments,
        RotationStore(tmp_path / "routing.db"),
        router_factory=ControlledRouter,
        **kwargs,
    )


@pytest.mark.parametrize(
    "failure",
    [
        ProviderError(429),
        ProviderError(503),
        ProviderError(500),
        ProviderError(408),
        TimeoutError(),
        ConnectionError(),
        ValueError("bad provider response"),
    ],
)
def test_immediate_failover_without_repeat_or_sleep(tmp_path, failure):
    router = service(tmp_path, sleeper=lambda _: pytest.fail("must not sleep"))

    def behavior(deployment, options):
        if deployment["model_info"]["id"] == router.deployments[0].id:
            raise failure
        return response()

    router.router.behavior = behavior
    result = router.structured_completion("A task")
    assert router.retries_per_deployment == 0
    assert [d for d, _ in router.router.calls] == [d.id for d in router.deployments[:2]]
    assert result["answer"] == "ok"
    assert result["diagnostics"]["attempt_count"] == 2
    assert result["diagnostics"]["attempts"][0]["retry_number"] == 0
    assert all(options["timeout"] <= 30 for _, options in router.router.calls)
    assert router.router.config["num_retries"] == 0
    assert router.router.config["max_fallbacks"] == 0


def test_legacy_retry_setting_cannot_repeat_provider_failure(tmp_path):
    router = service(
        tmp_path,
        retries_per_deployment=3,
        sleeper=lambda _: pytest.fail("must fail over immediately"),
    )
    router.router.behavior = lambda *_: (_ for _ in ()).throw(ProviderError(503))
    with pytest.raises(RoutingError) as error:
        router.structured_completion("A task")
    assert len(router.router.calls) == 4
    assert len(error.value.diagnostics["attempts"]) == 4


def test_remaining_budget_caps_attempt_and_stops_new_deployments(tmp_path):
    now = [0.0]
    router = service(tmp_path, clock=lambda: now[0])
    timeouts = []

    def behavior(deployment, options):
        timeouts.append(options["timeout"])
        now[0] += options["timeout"]
        raise ProviderError(504)

    router.router.behavior = behavior
    with pytest.raises(RoutingError) as error:
        router.structured_completion("A task")
    assert timeouts == [30, 30]
    assert now[0] == 60
    assert error.value.status_code == 504
    assert error.value.diagnostics["time_budget_exhausted"]
    assert len(error.value.diagnostics["attempts"]) == 2


def test_short_remaining_budget_is_forwarded_to_next_attempt(tmp_path):
    now = [0.0]
    router = service(tmp_path, clock=lambda: now[0], routing_time_budget=40)
    timeouts = []

    def behavior(deployment, options):
        timeouts.append(options["timeout"])
        now[0] += options["timeout"]
        raise ProviderError(503)

    router.router.behavior = behavior
    with pytest.raises(RoutingError) as error:
        router.structured_completion("A task")
    assert timeouts == [30, 10]
    assert error.value.diagnostics["time_budget_exhausted"]


def test_deployment_cap_and_model_unavailability_remain_enforced(tmp_path):
    router = service(tmp_path, max_attempts=1)
    router.router.behavior = lambda *_: (_ for _ in ()).throw(
        ProviderError(404, message="Model not found")
    )
    with pytest.raises(RoutingError) as error:
        router.structured_completion("A task")
    assert error.value.status_code == 404
    assert len(router.router.calls) == 1
    with router.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM unavailable").fetchone()[0] == 1
    router.router.behavior = lambda *_: response()
    assert router.structured_completion("Next")["diagnostics"]["selected_model"] == "model-1"


def test_hard_budget_bounds_sdk_that_ignores_timeout(tmp_path):
    router = service(tmp_path, request_timeout=0.15, routing_time_budget=0.25)
    release = threading.Event()
    completed = []

    def behavior(*_):
        release.wait(3)
        completed.append(True)
        return response()

    router.router.behavior = behavior
    start = time.monotonic()
    try:
        with pytest.raises(RoutingError) as error:
            router.structured_completion("A task")
        elapsed = time.monotonic() - start
        assert 0.20 <= elapsed < 1.0
        assert error.value.diagnostics["time_budget_exhausted"]
        assert len(router.router.calls) == 2
        snapshot = json.dumps(error.value.diagnostics, sort_keys=True)
    finally:
        release.set()
    until = time.monotonic() + 1
    while len(completed) < 2 and time.monotonic() < until:
        time.sleep(0.01)
    assert len(completed) == 2
    assert json.dumps(error.value.diagnostics, sort_keys=True) == snapshot


@pytest.mark.parametrize("retry_after", [None, "bad", "nan", "0", "120"])
def test_429_always_cools_down_and_next_request_skips_it(tmp_path, retry_after):
    router = service(tmp_path, count=2)
    first, second = router.deployments

    def behavior(deployment, options):
        if deployment["model_info"]["id"] == first.id:
            raise ProviderError(429, {"Retry-After": retry_after} if retry_after else {})
        return response()

    router.router.behavior = behavior
    router.structured_completion("First")
    router.structured_completion("Second")
    assert [d for d, _ in router.router.calls] == [first.id, second.id, second.id]
    with router.ledger.connect() as db:
        until = db.execute("SELECT until FROM cooldown WHERE bucket=?", (first.id,)).fetchone()[0]
    assert until > time.time() + (110 if retry_after == "120" else 20)


def test_round_robin_persists_and_specific_model_stays_scoped(tmp_path):
    router = service(tmp_path)
    assert router.structured_completion("First")["diagnostics"]["selected_model"] == "model-0"
    restarted = service(tmp_path)
    assert restarted.structured_completion("Second")["diagnostics"]["selected_model"] == "model-1"
    result = restarted.generate_content(
        model="model-3", messages=[{"role": "user", "content": "Hi"}]
    )
    assert result["diagnostics"]["selected_model"] == "model-3"
    assert restarted.router.calls[-1][1].get("response_format") is None


def test_access_denial_fails_over_but_enforcement_stops(tmp_path):
    router = service(tmp_path)
    router.router.behavior = lambda *_: (_ for _ in ()).throw(ProviderError(403))
    with pytest.raises(RoutingError):
        router.structured_completion("A task")
    assert len(router.router.calls) == 4
    with router.ledger.connect() as db:
        assert db.execute("SELECT count(*) FROM access_denials").fetchone()[0] == 4

    blocked_path = tmp_path / "blocked"
    blocked_path.mkdir()
    blocked = service(blocked_path)
    blocked.router.behavior = lambda *_: (_ for _ in ()).throw(
        ProviderError(403, message="CONSUMER_SUSPENDED")
    )
    with pytest.raises(RoutingError) as error:
        blocked.structured_completion("A task")
    assert error.value.status_code == 403
    assert len(blocked.router.calls) == 1
    with pytest.raises(RoutingError):
        blocked.structured_completion("Still blocked")
    assert len(blocked.router.calls) == 1


def test_invalid_response_fails_over_and_all_cooling_preserves_retry_after(tmp_path):
    router = service(tmp_path, count=2)
    router.router.behavior = lambda deployment, options: (
        response("invalid")
        if (deployment["model_info"]["id"] == router.deployments[0].id)
        else response()
    )
    assert router.structured_completion("A task")["answer"] == "ok"
    for deployment in router.deployments:
        router.ledger.failure(deployment, 429, "90")
    with pytest.raises(RoutingError) as error:
        router.structured_completion("Cooling")
    assert error.value.status_code == 429
    assert error.value.retry_after > 80
    assert len(router.router.calls) == 2


def test_settings_defaults_overrides_and_validation(tmp_path):
    env = tmp_path / ".env"
    base = 'GEMINI_PROJECTS=\'[{"label":"test","api_key":"secret"}]\'\n'
    env.write_text(base)
    router = load_service(env, ControlledRouter)
    assert (router.retries_per_deployment, router.request_timeout, router.routing_time_budget) == (
        0,
        30,
        60,
    )
    env.write_text(base + "PROVIDER_REQUEST_TIMEOUT=2.5\nCOMPLETION_TIME_BUDGET=4\n")
    router = load_service(env, ControlledRouter)
    assert (router.request_timeout, router.routing_time_budget) == (2.5, 4)
    for value in ("0", "-1", "nan", "inf", "invalid"):
        env.write_text(base + f"COMPLETION_TIME_BUDGET={value}\n")
        with pytest.raises(RoutingError):
            load_service(env, ControlledRouter)


@pytest.mark.parametrize("keyword", ["request_timeout", "routing_time_budget"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_limits_rejected(tmp_path, keyword, value):
    with pytest.raises(ValueError):
        service(tmp_path, **{keyword: value})
