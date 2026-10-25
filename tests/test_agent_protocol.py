"""The production structured decision path, through the real HTTP router."""

import asyncio
import json
from pathlib import Path

from langchain_classic.agents import AgentExecutor
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import StructuredTool

from ntg_llm import LLMClient
from ntg_llm.agent_protocol import build_react_agent, decision_schema, format_decision
from ntg_llm.langchain_adapter import NTGLangChainLLM
from ntg_llm.schemas import response_schema
from test_routing_http import endpoint, make_service


def decision(name, arguments=None, final=""):
    if isinstance(final, dict) and "files" in final:
        final = {"support_files": [], **final}
    return json.dumps(
        {
            "decision": {
                "action": name,
                "arguments": {"output": final} if name == "final_answer" else arguments or {},
            }
        }
    )


def test_structured_completion_is_unavailable_until_tool_evidence(tmp_path):
    observed = []

    async def execute(command: str):
        """Execute an actual project check."""
        observed.append(command)
        return "observed execution"

    tool = StructuredTool.from_function(coroutine=execute)
    replies = [
        decision("execute", {"command": "project-check"}),
        decision("final_answer", final="done"),
    ]
    with endpoint(replies) as (base, calls):
        llm = NTGLangChainLLM(
            client=LLMClient(service=make_service(tmp_path, base, ["/ok"])), structured_actions=True
        )
        agent = build_react_agent(
            llm,
            [tool],
            PromptTemplate.from_template("{tools}\n{tool_names}\n{input}\n{agent_scratchpad}"),
            finish_guard=lambda steps: bool(observed),
        )
        result = asyncio.run(AgentExecutor(agent=agent, tools=[tool]).ainvoke({"input": "do work"}))
    assert result["output"] == "done"
    assert observed == ["project-check"]
    first_schema = calls[0][1]["response_format"]["json_schema"]["schema"]
    second_schema = calls[1][1]["response_format"]["json_schema"]["schema"]
    assert '"const": "final_answer"' not in json.dumps(first_schema)
    assert '"const": "final_answer"' in json.dumps(second_schema)
    assert "stop" not in calls[0][1]  # Text stop sequences must never truncate JSON.


def test_decision_schema_is_closed_and_rejects_premature_completion():
    import pytest
    from pydantic import ValidationError

    def read(path: str):
        """Read a path."""
        return path

    contract = decision_schema([StructuredTool.from_function(read)], allow_finish=False)
    assert response_schema(contract)["additionalProperties"] is False
    with pytest.raises(ValidationError):
        contract.model_validate_json(decision("final_answer", final="invented PASS"))


def test_mcp_nested_arguments_and_code_are_typed_without_double_encoding():
    from types import SimpleNamespace

    tool = SimpleNamespace(
        name="edit_file",
        args_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                        },
                        "required": ["oldText", "newText"],
                    },
                },
                "dryRun": {"type": "boolean"},
            },
            "required": ["path", "edits"],
        },
    )
    contract = decision_schema([tool], allow_finish=False)
    payload = {
        "path": "module.ext",
        "edits": [{"oldText": "", "newText": 'one\n"two"\\three'}],
        "dryRun": None,
    }
    parsed = contract.model_validate_json(decision("edit_file", payload))
    assert json.loads(format_decision(parsed).split("Action Input: ", 1)[1]) == {
        key: value for key, value in payload.items() if value is not None
    }


def test_coverage_gap_requires_additional_executed_assertions(tmp_path):
    from tester_agent import TesterAgent
    from test_agent_workflow import ExecutionFixture

    replies = [json.dumps({"criteria": [{"quote": "preserve state", "expected": "Check state preservation", "kind": "behavior"}]})]
    for content, gaps in (
        ("assert basic", ["C1"]),
        ("assert basic; assert preservation", []),
    ):
        replies += [
            decision(
                "final_answer",
                final={"files": [{"path": "suite.check", "content": content, "runner": ["runner"], "cwd": ".", "timeout_seconds": 120}],
                       "coverage": ["C1"]},
            ),
            json.dumps({"missing_checks": gaps}),
        ]
    with endpoint(replies) as (base, calls):
        llm = NTGLangChainLLM(
            client=LLMClient(service=make_service(tmp_path, base, ["/ok"])), structured_actions=True
        )
        tester = TesterAgent(
            llm,
            [],
            tmp_path,
            executor_factory=lambda workspace: ExecutionFixture(
                workspace,
                {"success": True, "exit_code": 0, "stage": "command_execution"},
            ),
        )
        result = asyncio.run(tester.verify("Validate behavior and preserve state", "Done"))
    assert result.status == "PASS"
    assert len(result.evidence) == 2
    assert result.evidence[0]["superseded"]
    assert any("Check state preservation" in body["messages"][-1]["content"] for _, body in calls)


def test_coder_cannot_rewrite_independent_test_artifacts(tmp_path):
    from coder_agent import _protect_independent_tests

    writes = []

    async def write_file(path: str, content: str):
        """Write a source file."""
        writes.append(path)
        return "written"

    tools = _protect_independent_tests(
        [StructuredTool.from_function(coroutine=write_file)], tmp_path
    )
    rejected = asyncio.run(
        tools[0].ainvoke({"path": ".agent_tests/run/suite", "content": "weakened"})
    )
    assert json.loads(rejected)["success"] is False
    assert not writes
    assert asyncio.run(tools[0].ainvoke({"path": "source.ext", "content": "fixed"})) == "written"
    assert writes == ["source.ext"]


def test_invalid_structured_response_preserves_failure_stage_on_failover(tmp_path):
    from ntg_llm.schemas import Answer

    with endpoint(['{"answer":42}', '{"answer":"ok"}']) as (base, calls):
        client = LLMClient(service=make_service(tmp_path, base, ["/first", "/next"]))
        response = client.models.generate_content(
            model="router",
            contents="Reply",
            config={
                "response_mime_type": "application/json",
                "response_schema": Answer,
            },
        )
    assert response.parsed.answer == "ok"
    assert len(calls) == 2
    failure = response.diagnostics["failed_attempts"][0]
    assert failure["failure_stage"] == "response_validation"
    assert failure["exception_type"] == "ValidationError"
    assert failure["error"] == "Provider returned an invalid structured response"
    assert failure["retry_number"] == 0
    assert failure["validation_errors"][0] == {"path": ["answer"], "keyword": "type"}


def test_negative_probe_does_not_deadlock_coder_and_is_forwarded_to_tester(tmp_path):
    from coder_agent import create_coder_agent
    from agent_orchestrator import run_workflow
    from test_agent_workflow import Tester

    async def execute_in_sandbox(command: str):
        """Execute a project check."""
        code = 1 if command == "negative-probe" else 0
        return json.dumps(
            {
                "command": command,
                "success": code == 0,
                "exit_code": code,
                "stage": "command_execution",
            }
        )

    replies = [
        decision("execute_in_sandbox", {"command": "behavior-check"}),
        decision("execute_in_sandbox", {"command": "negative-probe"}),
        decision("final_answer", final="Implementation checked"),
    ]
    tester = Tester(["PASS"])
    with endpoint(replies) as (base, calls):
        llm = NTGLangChainLLM(client=LLMClient(service=make_service(tmp_path, base, ["/ok"])))
        coder = create_coder_agent(
            llm,
            [StructuredTool.from_function(coroutine=execute_in_sandbox)],
            tmp_path,
            verbose=False,
        )
        result = asyncio.run(run_workflow("Validate positive and invalid inputs", coder, tester))
    assert result.status == "PASS"
    assert len(calls) == 3
    assert '"exit_code": 1' in tester.requests[0][1]
    assert "negative-probe" in tester.requests[0][1]


def test_gemini_reasoning_setting_reaches_native_http_body(tmp_path):
    from ntg_llm.schemas import Deployment
    from ntg_llm.service import CompletionService
    from ntg_llm.store import RotationStore

    with endpoint(["reasoned response"], gemini=True) as (base, calls):
        service = CompletionService([Deployment("test", None, "gemini-3.8-flash", "local-test-key", api_base=base)],
                                    RotationStore(tmp_path / "native.sqlite3"))
        response = LLMClient(service=service).models.generate_content(model="router", contents="Reason carefully",
                                                                   config={"reasoning_effort": "low"})
    assert response.text == "reasoned response"
    assert calls[0][1]["generationConfig"]["thinkingConfig"]["thinkingLevel"].lower() == "low"
    assert "reasoning_effort" not in calls[0][1]


def test_reasoning_setting_is_validated_before_routing():
    import pytest
    from ntg_llm.generation import generation_request
    from ntg_llm.errors import RoutingError

    assert generation_request("Task", {"reasoning_effort": "low"})[2]["reasoning_effort"] == "low"
    with pytest.raises(RoutingError):
        generation_request("Task", {"reasoning_effort": "unbounded"})


def test_context_preserves_source_and_traceback_newlines():
    from ntg_llm.agent_protocol import context_text, observation_text

    source = "function sample() {\n  return 1;\n}"
    assert observation_text([{"type": "text", "text": source}]) == source
    assert "Traceback\nsecond line" in observation_text(json.dumps({
        "command": "project-check", "exit_code": 1, "stderr": "Traceback\nsecond line",
    }))
    assert context_text([]) == "[]"


def test_frozen_criteria_converge_and_keep_approved_suites(tmp_path):
    from tester_agent import TesterAgent
    from test_agent_workflow import ExecutionFixture

    def plan(path, content, coverage):
        return decision("final_answer", final={"coverage": coverage, "files": [{
            "path": path, "content": content, "runner": ["runner"], "cwd": ".", "timeout_seconds": 120,
        }]})
    replies = [json.dumps({"criteria": [
        {"quote": "alpha", "expected": "Support alpha", "kind": "behavior"}, {"quote": "beta", "expected": "Support beta", "kind": "behavior"},
    ]}), plan("base.check", "assert alpha", ["C1", "C2"]), json.dumps({"missing_checks": ["C2"]}),
        plan("beta.check", "assert beta", ["C2"]), json.dumps({"missing_checks": []})]
    with endpoint(replies) as (base, calls):
        llm = NTGLangChainLLM(client=LLMClient(service=make_service(tmp_path, base, ["/ok"])))
        tester = TesterAgent(llm, [], tmp_path, executor_factory=lambda workspace: ExecutionFixture(
            workspace, {"success": True, "exit_code": 0, "stage": "command_execution"},
        ))
        result = asyncio.run(tester.verify("Support alpha and beta", "Done"))
    assert result.status == "PASS"
    assert len(result.evidence) == 3  # Old approved suite is rerun with the new one.
    assert result.evidence[1]["test_file"] == result.evidence[0]["test_file"]
    assert result.evidence[2]["test_file"] != result.evidence[0]["test_file"]
    assert Path(result.test_files[0]).read_text() == "assert alpha"
    assert calls[-1][1]["response_format"]["json_schema"]["schema"]["properties"]["missing_checks"]["items"]["const"] == "C2"


def test_acceptance_contract_rejects_invented_requirements(tmp_path):
    from tester_agent import TesterAgent
    from test_agent_workflow import ExecutionFixture

    replies = [json.dumps({"criteria": [{"quote": "recover corrupted data", "expected": "Unrequested feature", "kind": "behavior"}]})] * 2
    with endpoint(replies) as (base, calls):
        tester = TesterAgent(NTGLangChainLLM(client=LLMClient(service=make_service(tmp_path, base, ["/ok"]))),
                             [], tmp_path, executor_factory=lambda workspace: ExecutionFixture(workspace, {}))
        result = asyncio.run(tester.verify("Support alpha", "Done"))
    assert result.status == "FAIL"
    assert result.failure_kind == "verification"
    assert result.evidence == []
    assert len(calls) == 2
