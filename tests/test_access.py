import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from types import SimpleNamespace as NS
import json
import pytest
from provider_errors import access_failure
from routing import RotationStore, CompletionService, RoutingError

from tests.helpers import FakeRouter, deployments


@pytest.mark.parametrize(
    "reason",
    [
        "API_KEY_INVALID",
        "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "SERVICE_DISABLED",
        "CONSUMER_SUSPENDED",
        "IAM_PERMISSION_DENIED",
    ],
)
def test_access_failure_persists_only_safe_reason(tmp_path, reason):
    class Forbidden(Exception):
        status_code = 403
        response = NS(
            json=lambda: {
                "error": {
                    "message": "mock-secret must never be printed",
                    "details": [{"reason": reason, "metadata": {"credential": "mock-secret"}}],
                }
            }
        )

    service = CompletionService(deployments(), RotationStore(tmp_path / "state.db"), FakeRouter)
    service.router.error = Forbidden("mock-secret")
    with pytest.raises(RoutingError) as error:
        service.structured_completion("hi")
    assert error.value.diagnostics["access_error"]["reason"] == reason
    assert "mock-secret" not in json.dumps(error.value.as_dict())
    assert service.blocked_diagnostics()["access_error"]["reason"] == reason
    with service.ledger.connect() as db:
        rows = db.execute("SELECT v FROM state WHERE k='blocked'").fetchall()
        rows += db.execute("SELECT diagnostics FROM access_denials").fetchall()
        assert rows
        assert all("mock-secret" not in row[0] for row in rows)


def test_permission_denial_skips_failed_model_and_continues_without_retry(tmp_path):
    class Forbidden(Exception):
        status_code = 403

    class RecoveringRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Forbidden("permission denied") if not self.calls else None
            return super().completion(**kwargs)

    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    sleeps = []
    service = CompletionService(ds, store, RecoveringRouter, sleeper=sleeps.append)
    result = service.structured_completion("hi")
    assert service.router.calls == [ds[0].id, ds[1].id]
    assert [a["status"] for a in result["diagnostics"]["attempts"]] == [403, 200]
    assert sleeps == []
    with store.connect() as db:
        assert db.execute("SELECT v FROM state WHERE k='blocked'").fetchone() is None
    for _ in range(len(ds) * 2):
        assert RotationStore(store.path).select(ds) != ds[0]


def test_old_generic_permission_stop_is_migrated_without_resetting_cursor(tmp_path):
    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    assert store.select(ds) == ds[0]
    store.failure(
        ds[0],
        403,
        None,
        diagnostics={**ds[0].diagnostics, "access_error": {"reason": "PERMISSION_DENIED"}},
    )
    store.migrate_permission_stop(ds)
    assert store.select(ds) == ds[1]
    with store.connect() as db:
        assert (
            db.execute("SELECT reason FROM unavailable WHERE deployment=?", (ds[0].id,)).fetchone()[
                0
            ]
            == "provider_access_denied"
        )
        assert db.execute("SELECT v FROM state WHERE k='blocked'").fetchone() is None


@pytest.mark.parametrize("reason", ["CONSUMER_SUSPENDED", "API_KEY_LEAKED"])
def test_explicit_enforcement_is_not_migrated_or_bypassed(tmp_path, reason):
    class Forbidden(Exception):
        status_code = 403
        response = NS(json=lambda: {"error": {"details": [{"reason": reason}]}})

    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    service = CompletionService(ds, store, FakeRouter)
    service.router.error = Forbidden("provider enforcement")
    with pytest.raises(RoutingError):
        service.structured_completion("hi")
    assert len(service.router.calls) == 1
    store.migrate_permission_stop(ds)
    with pytest.raises(RoutingError):
        store.select(ds)


def test_leaked_key_reason_is_distinct_from_invalid_json():
    assert (
        access_failure(Exception("Your API key was reported as leaked."), 403)["reason"]
        == "API_KEY_LEAKED"
    )
    assert access_failure(Exception("Bad JSON with text API_KEY_INVALID"), 502) is None


def test_blocked_probe_uses_only_blocked_deployment_then_restores(tmp_path):
    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    store.failure(ds[3], 403, None)
    service = CompletionService(ds, store, FakeRouter)
    result = service.probe_blocked()
    assert service.router.calls == [ds[3].id]
    assert result["routing_restored"] is True
    assert service.blocked_diagnostics() is None


def test_failed_probe_never_retries_or_falls_back(tmp_path):
    class Unavailable(Exception):
        status_code = 503

    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    store.failure(ds[3], 403, None)
    service = CompletionService(ds, store, FakeRouter)
    service.router.error = Unavailable()
    with pytest.raises(RoutingError):
        service.probe_blocked()
    assert service.router.calls == [ds[3].id]
    assert service.blocked_diagnostics() is not None


def test_old_blocked_state_reports_missing_original_reason(tmp_path):
    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    store.failure(ds[0], 403, None)
    with pytest.raises(RoutingError) as error:
        store.select(ds)
    assert "not retained" in error.value.diagnostics["access_error"]["explanation"]
