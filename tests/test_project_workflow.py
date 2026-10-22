"""Actual LangGraph repair transitions and observed-file evidence."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ntg_llm import LLMClient, LLMError
from ntg_llm.project_workflow import build_project_graph, run_verified_project
from ntg_llm.sandbox import DockerSandboxRunner, snapshot_id
from ntg_llm.testing_models import ExecutionPolicy, ExecutionReport, TestProfile, TesterFeedback
from tests.test_tool_agent import action, final, stack


IMAGE = "sha256:" + "1" * 64


class ObservingRunner(DockerSandboxRunner):
    def preflight(self, workspace, profile):
        pass

    def run(self, workspace, profile, trusted_tests=None):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            manifest = self.snapshot(workspace, profile, directory)
        content = (Path(workspace) / "app.py").read_text()
        passed = "return 4" in content
        return ExecutionReport(
            run_id="observed",
            snapshot_id=snapshot_id(manifest),
            kind="script",
            outcome="passed" if passed else "failed",
            exit_code=0 if passed else 1,
            stderr="" if passed else "AssertionError: expected 4, got 3",
        )


def test_real_graph_provider_feedback_drives_written_repair(tmp_path):
    root, service, _ = stack(
        tmp_path,
        [
            action("write_file", path="app.py", content="def answer(): return 3", overwrite=False),
            final(),
            {
                "summary": "Expected 4 but got 3",
                "evidence_fields": ["stderr"],
                "suggested_changes": ["Return 4"],
            },
            action("write_file", path="app.py", content="def answer(): return 4", overwrite=True),
            final(),
        ],
    )
    policy = ExecutionPolicy(image_id=IMAGE)
    result = run_verified_project(
        LLMClient(service=service),
        "Implement answer",
        workspace=root,
        profile=TestProfile(kind="script", entrypoint="app.py"),
        policy=policy,
        allow_overwrite=True,
        runner=ObservingRunner(policy),
    )
    assert result.status == "passed" and result.repairs_used == 1
    assert [r.outcome for r in result.reports] == ["failed", "passed"]
    assert result.reports[0].snapshot_id != result.reports[1].snapshot_id
    assert "expected 4, got 3" in service.router.histories[3][-1]["content"]
    assert result.diagnostics["tester"][0]["source"] == "model"
    assert result.diagnostics["tester"][1]["source"] == "execution"
    assert len(service.router.calls) == 5


@pytest.mark.parametrize("repairs", [0, 1, 3])
@pytest.mark.parametrize("outcome", ["failed", "timeout", "infrastructure_error"])
def test_exact_repair_ceiling_and_infrastructure_stop(repairs, outcome):
    calls = []
    coder = SimpleNamespace(run=lambda *a, **kw: calls.append(kw) or "coder result")
    report = ExecutionReport(run_id="run", snapshot_id="hash", kind="script", outcome=outcome)
    runner = SimpleNamespace(run=lambda *a: report)
    tester = SimpleNamespace(
        assess=lambda *a: (
            TesterFeedback(summary="Observed", evidence_fields=["outcome"], suggested_changes=[]),
            {},
        )
    )
    graph = build_project_graph(
        coder,
        tester,
        runner,
        ".",
        TestProfile(kind="script", entrypoint="a.py"),
        None,
        "Task",
        None,
        repairs,
    )
    result = graph.invoke(
        {"generation_count": 0, "reports": [], "feedback": [], "tester_diagnostics": []},
        config={"recursion_limit": 3 * (repairs + 1) + 1},
    )
    expected = 1 if outcome == "infrastructure_error" else repairs + 1
    assert len(calls) == len(result["reports"]) == expected
    assert result["status"] == (
        "blocked" if outcome == "infrastructure_error" else "repair_exhausted"
    )


@pytest.mark.parametrize("limit", [-1, 11, True, "3"])
def test_invalid_repair_policy_never_calls_runner(limit):
    with pytest.raises(LLMError):
        run_verified_project(
            None, "task", workspace=".", profile=None, policy=None, max_repair_attempts=limit
        )


def test_snapshot_mismatch_blocks_success(tmp_path):
    root, service, _ = stack(tmp_path, [final()])
    (root / "app.py").write_text("def answer(): return 4")
    policy = ExecutionPolicy(image_id=IMAGE)

    class ChangingRunner(ObservingRunner):
        def run(self, *args):
            report = super().run(*args)
            (root / "app.py").write_text("pass")
            return report

    result = run_verified_project(
        LLMClient(service=service),
        "Inspect",
        workspace=root,
        profile=TestProfile(kind="script", entrypoint="app.py"),
        policy=policy,
        max_repair_attempts=0,
        runner=ChangingRunner(policy),
    )
    assert result.status == "blocked"
    assert result.reports[0].error_code == "stale_workspace"


def test_provider_failure_after_first_test_retains_evidence_and_written_file(tmp_path):
    class Limited(Exception):
        status_code = 429
        headers = {"Retry-After": "15"}

    root, service, _ = stack(
        tmp_path,
        [
            action("write_file", path="app.py", content="def answer(): return 3", overwrite=False),
            final(),
            {
                "summary": "Wrong result",
                "evidence_fields": ["stderr"],
                "suggested_changes": ["Return 4"],
            },
            Limited("private provider details"),
        ],
    )
    policy = ExecutionPolicy(image_id=IMAGE)
    with pytest.raises(LLMError) as error:
        run_verified_project(
            LLMClient(service=service),
            "Implement answer",
            workspace=root,
            profile=TestProfile(kind="script", entrypoint="app.py"),
            policy=policy,
            allow_overwrite=True,
            runner=ObservingRunner(policy),
        )
    assert error.value.status_code == 429 and error.value.retry_after == "15"
    assert error.value.diagnostics["project_outcomes"] == ["failed"]
    assert error.value.diagnostics["workspace_changes_may_remain"]
    assert (root / "app.py").read_text() == "def answer(): return 3"


def test_final_allowed_repair_can_pass(tmp_path):
    root, service, _ = stack(
        tmp_path,
        [
            action("write_file", path="app.py", content="def answer(): return 4", overwrite=False),
            final(),
            {
                "summary": "Wrong result",
                "evidence_fields": ["stderr"],
                "suggested_changes": ["Fix result"],
            },
            final(),
        ],
    )
    policy = ExecutionPolicy(image_id=IMAGE)

    class LastAttemptRunner(ObservingRunner):
        count = 0

        def run(self, *args):
            self.count += 1
            result = super().run(*args)
            return (
                result.model_copy(update={"outcome": "failed", "stderr": "first failure"})
                if self.count == 1
                else result
            )

    runner = LastAttemptRunner(policy)
    result = run_verified_project(
        LLMClient(service=service),
        "Implement answer",
        workspace=root,
        profile=TestProfile(kind="script", entrypoint="app.py"),
        policy=policy,
        allow_overwrite=True,
        max_repair_attempts=1,
        runner=runner,
    )
    assert result.status == "passed" and result.repairs_used == 1
    assert runner.count == 2


def test_concurrent_workflows_keep_evidence_isolated(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from tests.helpers import FakeRouter, deployments
    from tests.test_tool_agent import observations
    from ntg_llm.service import CompletionService
    from ntg_llm.store import RotationStore
    import json

    class PerRunRouter(FakeRouter):
        def completion(self, **kwargs):
            decision = (
                final()
                if observations(kwargs["messages"])
                else action(
                    "write_file", path="app.py", content="def answer(): return 4", overwrite=False
                )
            )
            selected = self.strategy.get_available_deployment(**kwargs)
            self.calls.append(selected["model_info"]["id"])
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(decision)))]
            )

    service = CompletionService(deployments(), RotationStore(tmp_path / "state.db"), PerRunRouter)
    policy = ExecutionPolicy(image_id=IMAGE)

    def run(index):
        root = tmp_path / f"workspace-{index}"
        root.mkdir()
        return run_verified_project(
            LLMClient(service=service),
            f"Implement answer {index}",
            workspace=root,
            profile=TestProfile(kind="script", entrypoint="app.py"),
            policy=policy,
            max_repair_attempts=0,
            runner=ObservingRunner(policy),
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert all(result.status == "passed" and len(result.reports) == 1 for result in results)
    assert all(
        result.diagnostics["coder"][0]["tool_calls"][0]["call_id"] == "tool_001"
        for result in results
    )
