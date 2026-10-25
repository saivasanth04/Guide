import asyncio
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from langchain_core.language_models.fake import FakeListLLM

from agent_orchestrator import repair_limit, run_workflow
from tester_agent import TesterAgent as IndependentTester, VerificationResult


class Coder:
    def __init__(self):
        self.requests = []

    async def ainvoke(self, request):
        self.requests.append(request["input"])
        return {"output": "Implementation complete"}


class Tester:
    __test__ = False

    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.requests = []

    async def verify(self, task, summary):
        self.requests.append((task, summary))
        status = next(self.statuses)
        return VerificationResult(
            status=status,
            summary="Observed result",
            evidence=[
                {
                    "command": "project-specific test",
                    "exit_code": 0 if status == "PASS" else 1,
                    "stdout": "actual output",
                    "stderr": "broken behavior" if status == "FAIL" else "",
                }
            ],
        )


@pytest.mark.parametrize(
    "statuses, limit, expected, repairs",
    [
        (["PASS"], 3, "PASS", 0),
        (["FAIL", "PASS"], 3, "PASS", 1),
        (["FAIL"] * 4, 3, "FAIL", 3),
        (["FAIL"], 0, "FAIL", 0),
    ],
)
def test_workflow_pass_repair_and_strict_termination(statuses, limit, expected, repairs):
    coder, tester = Coder(), Tester(statuses)
    result = asyncio.run(
        run_workflow("Original requirements", coder, tester, max_repair_attempts=limit)
    )
    assert result.status == expected
    assert result.repair_attempts == repairs
    assert len(coder.requests) == len(tester.requests) == len(statuses)
    assert len(result.history) == len(statuses)
    assert result.tester == result.history[-1]
    if repairs:
        assert "broken behavior" in coder.requests[1]
        assert "Original requirements" in coder.requests[1]
    if expected == "FAIL":
        assert result.error == "Maximum repair attempts exhausted"


@pytest.mark.parametrize("value", [-1, True, 1.5, "3"])
def test_invalid_cycle_limit_rejected(value):
    with pytest.raises(ValueError):
        asyncio.run(run_workflow("Task", Coder(), Tester(["PASS"]), max_repair_attempts=value))


def test_configurable_limit(monkeypatch):
    monkeypatch.setenv("MAX_REPAIR_ATTEMPTS", "3")
    assert repair_limit() == 3
    monkeypatch.setenv("MAX_REPAIR_ATTEMPTS", "0")
    assert repair_limit() == 0
    monkeypatch.setenv("MAX_REPAIR_ATTEMPTS", "-1")
    with pytest.raises(ValueError):
        repair_limit()


class ExecutionFixture:
    def __init__(self, workspace, observation):
        self.observation = observation
        self.closed = False

    def execute(self, command, timeout_seconds, *, protect_source):
        assert protect_source is True
        return json.dumps({"command": command, **self.observation})

    def as_tool(self):
        return SimpleNamespace(description="Execute in isolated sandbox")

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "observation, expected",
    [
        (
            {
                "success": True,
                "exit_code": 0,
                "stdout": "tested",
                "stderr": "",
                "stage": "command_execution",
            },
            "PASS",
        ),
        (
            {
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "real failure",
                "stage": "command_execution",
            },
            "FAIL",
        ),
        (
            {
                "success": False,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "error": "TimeoutException",
            },
            "FAIL",
        ),
        ({"success": True, "exit_code": 0, "stage": "dependency_installation"}, "FAIL"),
        (
            {
                "success": True,
                "exit_code": 0,
                "stage": "command_execution",
                "source_changes": ["./production.file"],
            },
            "FAIL",
        ),
    ],
)
def test_tester_verdict_is_bound_to_real_observations(tmp_path, observation, expected):
    fixtures = []

    def factory(workspace):
        instance = ExecutionFixture(workspace, observation)
        fixtures.append(instance)
        return instance

    llm = FakeListLLM(
        responses=[
            'Action: write_test_file\nAction Input: {"path":"suite.check","content":"assert behavior"}',
            'Action: run_test_file\nAction Input: {"path":"suite.check","runner":["runner"]}',
            'Final Answer: {"status":"PASS","summary":"Claimed success"}',
        ]
    )
    tester = IndependentTester(llm, [], tmp_path, executor_factory=factory)
    result = asyncio.run(tester.verify("Requirements", "Coder says done"))
    assert result.status == expected
    assert result.evidence[0]["command"].endswith("/suite.check")
    assert Path(result.test_files[0]).is_file()
    assert result.evidence[0].get("stderr") == observation.get("stderr")
    assert fixtures[0].closed


@pytest.mark.parametrize(
    "output",
    [
        '{"status":"PASS","summary":"No execution"}',
        '{"status":"PASS","summary":"Invented","evidence":[{"exit_code":0}]}',
        "not structured JSON",
    ],
)
def test_tester_cannot_pass_without_execution_or_invent_evidence(tmp_path, output):
    llm = FakeListLLM(responses=["Final Answer: " + output])
    tester = IndependentTester(
        llm, [], tmp_path, executor_factory=lambda workspace: ExecutionFixture(workspace, {})
    )
    result = asyncio.run(tester.verify("Task", "Summary"))
    assert result.status == "FAIL"
    assert result.evidence == []


def test_tester_only_receives_read_only_filesystem_tools(tmp_path):
    tools = [
        SimpleNamespace(name=name)
        for name in [
            "write_file",
            "edit_file",
            "move_file",
            "create_directory",
            "read_text_file",
            "list_directory",
        ]
    ]
    tester = IndependentTester(None, tools, tmp_path)
    assert [tool.name for tool in tester.filesystem_tools] == ["read_text_file", "list_directory"]


def test_multiple_sandbox_commands_do_not_consume_repair_cycles(tmp_path):
    llm = FakeListLLM(
        responses=[
            'Action: write_test_file\nAction Input: {"path":"suite.check","content":"assert behavior"}',
            'Action: run_test_file\nAction Input: {"path":"suite.check","runner":["runner"]}',
            'Action: run_test_file\nAction Input: {"path":"suite.check","runner":["runner"]}',
            'Final Answer: {"status":"PASS","summary":"Both behaviors validated"}',
        ]
    )
    tester = IndependentTester(
        llm,
        [],
        tmp_path,
        executor_factory=lambda workspace: ExecutionFixture(
            workspace,
            {
                "success": True,
                "exit_code": 0,
                "stdout": "checked",
                "stderr": "",
                "stage": "command_execution",
            },
        ),
    )
    coder = Coder()
    result = asyncio.run(run_workflow("Task", coder, tester, max_repair_attempts=0))
    assert result.status == "PASS"
    assert result.repair_attempts == 0
    assert len(result.tester.evidence) == 2
    assert len(coder.requests) == 1


def test_coder_repair_error_retains_latest_tester_evidence():
    class FailingRepair(Coder):
        async def ainvoke(self, request):
            if self.requests:
                raise RuntimeError("unavailable")
            return await super().ainvoke(request)

    result = asyncio.run(run_workflow("Task", FailingRepair(), Tester(["FAIL"])))
    assert result.status == "FAIL"
    assert result.tester.evidence[0]["stderr"] == "broken behavior"
    assert result.error.startswith("Coder failed")


def test_inline_execution_alone_cannot_pass(tmp_path):
    llm = FakeListLLM(
        responses=[
            'Action: execute_in_sandbox\nAction Input: {"command":"manual check"}',
            'Final Answer: {"status":"PASS","summary":"Claimed pass"}',
        ]
    )
    tester = IndependentTester(
        llm,
        [],
        tmp_path,
        executor_factory=lambda workspace: ExecutionFixture(
            workspace, {"success": True, "exit_code": 0, "stage": "command_execution"}
        ),
    )
    result = asyncio.run(tester.verify("Task", "Done"))
    assert result.status == "FAIL"
    assert "No saved test file" in result.summary


def test_test_writer_cannot_modify_project_source(tmp_path):
    source = tmp_path / "production.file"
    source.write_text("original")
    llm = FakeListLLM(
        responses=[
            'Action: write_test_file\nAction Input: {"path":"../../production.file","content":"changed"}',
            'Final Answer: {"status":"PASS","summary":"Claimed pass"}',
        ]
    )
    tester = IndependentTester(
        llm, [], tmp_path, executor_factory=lambda workspace: ExecutionFixture(workspace, {})
    )
    result = asyncio.run(tester.verify("Task", "Done"))
    assert result.status == "FAIL"
    assert result.test_files == []
    assert source.read_text() == "original"


def test_corrected_harness_can_pass_without_old_failure_poisoning_result(tmp_path):
    class SequenceExecution(ExecutionFixture):
        def execute(self, command, timeout_seconds, *, protect_source):
            previous = getattr(self, "attempts", 0)
            self.attempts = previous + 1
            self.observation = {
                "success": bool(previous),
                "exit_code": 0 if previous else 1,
                "stdout": "checked" if previous else "",
                "stderr": "wrong harness path" if not previous else "",
                "stage": "command_execution",
            }
            return super().execute(command, timeout_seconds, protect_source=protect_source)

    responses = []
    for content in ("wrong path", "correct path"):
        responses += [
            "Action: write_test_file\nAction Input: "
            + json.dumps({"path": "suite.check", "content": content}),
            'Action: run_test_file\nAction Input: {"path":"suite.check","runner":["runner"]}',
            'Final Answer: {"status":"PASS","summary":"Claimed pass"}',
        ]
    tester = IndependentTester(
        FakeListLLM(responses=responses),
        [],
        tmp_path,
        executor_factory=lambda workspace: SequenceExecution(workspace, {}),
    )
    result = asyncio.run(tester.verify("Task", "Done"))
    assert result.status == "PASS"
    assert len(result.evidence) == 2
    assert result.evidence[0]["superseded"]
    assert result.evidence[0]["test_sha256"] != result.evidence[1]["test_sha256"]


def test_verification_failure_does_not_request_production_repairs():
    class BrokenTester:
        async def verify(self, task, summary):
            return VerificationResult(
                status="FAIL", summary="No execution evidence", failure_kind="verification"
            )

    coder = Coder()
    result = asyncio.run(run_workflow("Task", coder, BrokenTester()))
    assert len(coder.requests) == 1
    assert result.repair_attempts == 0
    assert result.error == "Independent verification could not complete"


def test_coder_without_self_verification_has_one_bounded_continuation():
    class Unverified(Coder):
        async def ainvoke(self, request):
            result = await super().ainvoke(request)
            result["intermediate_steps"] = []
            return result

    coder = Unverified()
    tester = Tester(["PASS"])
    result = asyncio.run(run_workflow("Task", coder, tester))
    assert result.status == "FAIL"
    assert len(coder.requests) == 2
    assert not tester.requests
    assert "CoderSelfVerificationError" in result.error


def test_self_verification_must_follow_last_source_edit():
    from agent_orchestrator import has_successful_execution

    execution = (
        SimpleNamespace(tool="execute_in_sandbox"),
        json.dumps({"success": True, "exit_code": 0, "stage": "command_execution"}),
    )
    edit = (SimpleNamespace(tool="write_file"), "written")
    assert has_successful_execution({"intermediate_steps": [edit, execution]})
    assert not has_successful_execution({"intermediate_steps": [execution, edit]})
    failed = (SimpleNamespace(tool="execute_in_sandbox"), json.dumps({"success": False, "exit_code": 1}))
    # Negative-input probes can legitimately exit nonzero. The independent
    # Tester receives them as evidence instead of an earlier success hiding them.
    completed = {"intermediate_steps": [execution, failed]}
    assert has_successful_execution(completed)
    from agent_orchestrator import execution_evidence
    assert execution_evidence(completed)[-1]["exit_code"] == 1


def test_unexpected_tester_exception_does_not_consume_coder_repairs():
    class BrokenTester:
        async def verify(self, task, summary):
            raise RuntimeError("transport unavailable")

    coder = Coder()
    result = asyncio.run(run_workflow("Task", coder, BrokenTester()))
    assert result.tester.failure_kind == "infrastructure"
    assert len(coder.requests) == 1
