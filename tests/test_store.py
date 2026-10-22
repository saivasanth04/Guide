import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import json
from routing import Deployment, RotationStore, CompletionService

from tests.helpers import FakeRouter, deployments


def test_round_robin_has_no_local_request_limit(tmp_path):
    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    for index in range(2000):
        assert store.select(ds) == ds[index % len(ds)]


def test_concurrent_round_robin_is_atomic(tmp_path):
    ds = deployments()
    path = tmp_path / "state.db"
    RotationStore(path)
    with ThreadPoolExecutor(max_workers=12) as pool:
        selected = list(pool.map(lambda _: RotationStore(path).select(ds).id, range(120)))
    assert all(selected.count(d.id) == 20 for d in ds)


def test_old_usage_counters_cannot_block_rotation(tmp_path):
    ds = deployments()
    store = RotationStore(tmp_path / "state.db")
    with store.connect() as db:
        db.execute("CREATE TABLE usage(bucket TEXT, ts REAL, day TEXT, tokens INTEGER)")
        db.execute("INSERT INTO usage VALUES(?,?,?,?)", (ds[0].id, store.clock(), "today", 10**12))
        db.execute(
            "INSERT INTO usage VALUES(?,?,?,?)",
            ("unidentified-projects", store.clock(), "today", 10**12),
        )
    assert store.select(ds) == ds[0]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM usage").fetchone()[0] == 2


def test_large_prompts_and_reported_token_usage_are_not_locally_limited(tmp_path):
    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.kwargs = kwargs
            response = super().completion(**kwargs)
            response.usage = NS(prompt_tokens=10**7, total_tokens=10**8)
            return response

    service = CompletionService(
        deployments(), RotationStore(tmp_path / "state.db"), RecordingRouter
    )
    assert service.structured_completion("x" * 100000)["answer"] == "actual answer"
    assert "max_tokens" not in service.router.kwargs
    assert service.structured_completion("again")["answer"] == "actual answer"


def test_rotation_persistence(tmp_path):
    ds = deployments()
    path = tmp_path / "quota.db"
    for n in range(14):
        assert RotationStore(path).select(ds).id == ds[n % 6].id


def test_pinned_retry_does_not_rewind_concurrent_rotation(tmp_path):
    ledger = RotationStore(tmp_path / "quota.db")
    ds = deployments()
    assert ledger.select(ds) == ds[0]
    assert ledger.select(ds) == ds[1]
    assert ledger.select(ds, only=ds[0].id) == ds[0]
    assert RotationStore(ledger.path).select(ds) == ds[2]


def test_legacy_rotation_position_survives_model_replacement(tmp_path):
    ds = deployments()
    ledger = RotationStore(tmp_path / "quota.db")
    old_id = json.dumps([ds[0].project_id, "gemini-2.5-flash"], separators=(",", ":"))
    with ledger.connect() as db:
        db.execute("INSERT INTO state VALUES('cursor',?)", (json.dumps(old_id),))
    assert ledger.select(ds) == ds[3]


def test_mixed_provider_state_persists_when_deployment_is_removed(tmp_path):
    ledger = RotationStore(tmp_path / "quota.db")
    a = Deployment("one", None, "same-model", "mock-secret", provider="groq")
    b = Deployment("two", None, "same-model", "mock-secret", provider="openrouter")
    assert ledger.select([a, b]) == a
    assert RotationStore(ledger.path).select([b]) == b
    assert RotationStore(ledger.path).select([a, b]) == a


def test_replaced_third_model_preserves_rotation_position(tmp_path):
    ds = deployments()
    ledger = RotationStore(tmp_path / "state.db")
    old_id = json.dumps([ds[0].project_id, "gemini-3.6-flash"], separators=(",", ":"))
    with ledger.connect() as db:
        db.execute("INSERT INTO state VALUES('cursor',?)", (json.dumps(old_id),))
    assert ledger.select(ds) == ds[3]
    assert ds[2].model == "gemini-3.1-flash-lite"
