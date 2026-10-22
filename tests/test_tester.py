from types import SimpleNamespace

import pytest

from ntg_llm import LLMError
from ntg_llm.tester import TesterAgent
from ntg_llm.testing_models import ExecutionReport, TestProfile, TesterFeedback


@pytest.mark.parametrize("outcome", ["passed", "infrastructure_error"])
def test_host_terminal_verdict_uses_no_model(outcome):
    tester = TesterAgent(None)
    tester.agent = SimpleNamespace(run=lambda *a, **k: pytest.fail("No model needed"))
    feedback, diagnostics = tester.assess(
        "task",
        TestProfile(kind="script", entrypoint="a.py"),
        ExecutionReport(run_id="one", snapshot_id="hash", kind="script", outcome=outcome),
    )
    assert outcome in feedback.summary and diagnostics["source"] == "execution"


@pytest.mark.parametrize("failure", ["provider", "invented_field"])
def test_tester_failure_keeps_execution_evidence(failure):
    def run(*args, **kwargs):
        if failure == "provider":
            raise LLMError(429, "Unavailable", {"attempt_count": 1})
        return SimpleNamespace(
            output=TesterFeedback(
                summary="Wrong", evidence_fields=["invented"], suggested_changes=[]
            )
        )

    tester = TesterAgent(None)
    tester.agent = SimpleNamespace(run=run)
    feedback, diagnostics = tester.assess(
        "task",
        TestProfile(kind="script", entrypoint="a.py"),
        ExecutionReport(
            run_id="one", snapshot_id="hash", kind="script", outcome="failed", stderr="NameError"
        ),
    )
    assert "failed" in feedback.summary and diagnostics["source"] == "fallback"
