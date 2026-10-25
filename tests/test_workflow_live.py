"""Deterministic agent decisions over the real router, MCP filesystem, and live E2B.

Set NTG_LIVE_E2B=1 to run. Provider responses are controlled locally so failure and
exhaustion paths are reproducible; all file edits, executions, and evidence are real.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil

import pytest

from agent_orchestrator import run_workflow
from coder_agent import create_coder_agent, filesystem_config
from e2b_sandbox import E2BSandboxExecutor
from langchain_mcp_adapters.client import MultiServerMCPClient
from ntg_llm import LLMClient
from ntg_llm.langchain_adapter import NTGLangChainLLM
from tester_agent import TesterAgent as IndependentTester
from test_routing_http import endpoint, make_service


pytestmark = pytest.mark.skipif(
    os.getenv("NTG_LIVE_E2B") != "1" or not shutil.which("npx"),
    reason="Set NTG_LIVE_E2B=1 with E2B credentials and npx",
)


def action(name, **arguments):
    return f"Action: {name}\nAction Input: " + json.dumps(arguments)


@pytest.mark.parametrize("scenario", ["pass", "repair", "exhaustion"])
def test_live_coder_tester_workflows(tmp_path, scenario):
    workspace = tmp_path / "project"
    workspace.mkdir()
    python = scenario == "repair"
    destination = workspace / ("arithmetic.py" if python else "arithmetic.js")
    correct = (
        "def combine(a, b):\n    return a + b\n"
        if python
        else "module.exports = (a, b) => a + b;\n"
    )
    broken = correct.replace("a + b", "a - b")
    self_check = "python -m py_compile arithmetic.py" if python else "node -c arithmetic.js"
    behavior_check = (
        'python -c "from arithmetic import combine; assert combine(2,3)==5; '
        'assert combine(-2,2)==0; print(12345)"'
        if python
        else '''node -e "const a=require('node:assert/strict'); const f=require('./arithmetic.js'); a.equal(f(2,3),5); a.equal(f(-2,2),0); console.log(12345)"'''
    )
    rounds = 1 if scenario == "pass" else 2 if scenario == "repair" else 4
    def native(name, arguments):
        return json.dumps({"decision": {"action": name, "arguments": arguments}})

    replies = []
    for index in range(rounds):
        source = correct if scenario == "pass" or (scenario == "repair" and index > 0) else broken
        if index:
            replies.append(native("read_text_file", {"path": str(destination), "head": None, "tail": None}))
        replies += [
            native("write_file", {"path": str(destination), "content": source}),
            native("execute_in_sandbox", {"command": self_check, "timeout_seconds": 120}),
            native("final_answer", {"output": "Implementation updated and syntax verified."}),
        ]
        if index == 0:
            replies.append(json.dumps({"criteria": [{"quote": "add two numbers", "expected": "Adds positive and negative numbers", "kind": "behavior"}]}))
        replies += [
            native("read_text_file", {"path": str(destination), "head": None, "tail": None}),
            native("final_answer", {"output": {"files": [{
                "path": "check_behavior.py",
                "content": "import subprocess\nsubprocess.run(" + repr(behavior_check) + ", shell=True, check=True)\n",
                "runner": ["python"], "cwd": ".", "timeout_seconds": 120,
            }], "support_files": [], "coverage": ["C1"]}}),
            json.dumps({"missing_checks": []}) if source == correct else json.dumps({
                "status": "FAIL", "summary": "Independent behavioral assertions failed",
                "failure_kind": "implementation",
            }),
        ]
    task = "Implement combine(a, b) to add two numbers, including negative inputs."
    with endpoint(replies) as (base, calls):
        service = make_service(tmp_path, base, ["/ok"], request_timeout=10, routing_time_budget=20)
        llm = NTGLangChainLLM(client=LLMClient(service=service), structured_actions=True)

        async def run():
            filesystem_tools = await MultiServerMCPClient(
                filesystem_config(str(workspace))
            ).get_tools()
            executor = E2BSandboxExecutor(workspace)
            try:
                coder = create_coder_agent(
                    llm, [*filesystem_tools, executor.as_tool()], workspace, verbose=False
                )
                tester = IndependentTester(llm, filesystem_tools, workspace)
                result = await run_workflow(task, coder, tester, max_repair_attempts=3)
                assert executor._sandbox is not None  # Coder retains execution/self-verification.
                coder_sandbox_id = executor._sandbox.sandbox_id
            finally:
                await asyncio.to_thread(executor.close)
            return result, coder_sandbox_id

        result, coder_sandbox_id = asyncio.run(run())
        assert result.status == ("FAIL" if scenario == "exhaustion" else "PASS"), result
        assert result.repair_attempts == rounds - 1
        assert len(result.history) == rounds
        assert not replies
        assert all(report.evidence for report in result.history)
        ids = [report.evidence[0]["sandbox_id"] for report in result.history]
        assert coder_sandbox_id not in ids
        assert len(set(ids)) == rounds  # Each independent verification starts fresh.
        if scenario != "pass":
            assert result.history[0].evidence[0]["exit_code"] != 0
            assert result.history[0].evidence[0]["stderr"]
            assert any("tester_failure" in body["messages"][-1]["content"] for _, body in calls)
        if result.status == "PASS":
            assert result.tester.evidence[0]["exit_code"] == 0
            assert result.tester.evidence[0]["stdout"].strip() == "12345"
        else:
            assert result.error == "Maximum repair attempts exhausted"
            assert result.tester.evidence[0]["stderr"]


def test_live_shell_does_not_hide_failures(tmp_path):
    executor = E2BSandboxExecutor(tmp_path)
    try:
        for command in ("false; echo hidden", "false | cat"):
            result = json.loads(executor.execute(command))
            assert result["exit_code"] != 0
            assert result["success"] is False
        expected = json.loads(executor.execute("if false; then exit 1; else echo expected; fi"))
        assert expected["success"] is True
        assert expected["stdout"].strip() == "expected"
    finally:
        executor.close()


def test_live_runner_owns_test_path_and_project_cwd(tmp_path):
    project = tmp_path / "project with spaces"
    project.mkdir()
    (project / "marker.txt").write_text("expected")
    replies = [
        action("write_test_file", path="suite with spaces.py", content="from pathlib import Path\nassert Path('marker.txt').read_text() == 'expected'\nprint('verified cwd')\n"),
        action("run_test_file", path="suite with spaces.py", runner=["python", "-u"], cwd="project with spaces"),
        'Final Answer: {"status":"PASS","summary":"Actual test and cwd verified"}',
    ]
    with endpoint(replies) as (base, _):
        tester = IndependentTester(
            NTGLangChainLLM(client=LLMClient(service=make_service(tmp_path, base, ["/ok"])), structured_actions=False),
            [], tmp_path,
        )
        result = asyncio.run(tester.verify("Read marker.txt from the project", "Done"))
    assert result.status == "PASS", result
    assert result.evidence[0]["stdout"].strip() == "verified cwd"
    assert result.evidence[0]["cwd"].endswith("/project with spaces")


def test_live_module_runner_imports_staged_tests(tmp_path):
    (tmp_path / "marker.txt").write_text("expected")
    source = "import unittest\nfrom pathlib import Path\nclass Behavior(unittest.TestCase):\n    def test_marker(self):\n        self.assertEqual(Path('marker.txt').read_text(), 'expected')\n"
    replies = [json.dumps({"criteria": [{"quote": "Read marker.txt", "expected": "Read actual contents", "kind": "behavior"}]}),
        json.dumps({"decision": {"action": "final_answer", "arguments": {"output": {
            "coverage": ["C1"], "support_files": [], "files": [{"path": "test_marker.py", "cwd": ".",
                "runner": ["python", "-m", "unittest"], "timeout_seconds": 120, "content": source}],
        }}}}), json.dumps({"missing_checks": []})]
    with endpoint(replies) as (base, _):
        tester = IndependentTester(NTGLangChainLLM(client=LLMClient(service=make_service(tmp_path, base, ["/ok"]))), [], tmp_path)
        result = asyncio.run(tester.verify("Read marker.txt", "Done"))
    assert result.status == "PASS", result
    assert "Ran 1 test" in result.evidence[0]["stderr"]
    assert result.evidence[0]["exit_code"] == 0


def test_live_tester_installs_declared_test_dependencies(tmp_path):
    (tmp_path / "marker.txt").write_text("expected")
    replies = [json.dumps({"criteria": [{"quote": "Read marker.txt", "expected": "Read actual contents", "kind": "behavior"}]}),
        json.dumps({"decision": {"action": "final_answer", "arguments": {"output": {
            "coverage": ["C1"],
            "support_files": [{"path": "requirements.txt", "content": "pytest>=8,<9\n"}],
            "files": [{"path": "test_marker.py", "cwd": ".", "runner": ["python", "-m", "pytest"],
                       "timeout_seconds": 120,
                       "content": "from pathlib import Path\ndef test_marker():\n    assert Path('marker.txt').read_text() == 'expected'\n"}],
        }}}}), json.dumps({"missing_checks": []})]
    with endpoint(replies) as (base, _):
        tester = IndependentTester(NTGLangChainLLM(client=LLMClient(service=make_service(tmp_path, base, ["/ok"]))), [], tmp_path)
        result = asyncio.run(tester.verify("Read marker.txt", "Done"))
    assert result.status == "PASS", result
    evidence = result.evidence[0]
    assert evidence["exit_code"] == 0
    assert "1 passed" in evidence["stdout"]
    assert any("pip install -r requirements.txt" in item["command"] for item in evidence["dependency_installations"])
    assert not (tmp_path / "requirements.txt").exists()  # Test-only manifest stays isolated.


def test_live_tester_source_mutation_cannot_turn_failure_into_pass(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    destination = workspace / "behavior.txt"
    destination.write_text("original")
    replies = [
        json.dumps({"criteria": [{"quote": "Verify existing behavior", "expected": "Preserve original project files", "kind": "behavior"}]}),
        json.dumps({"decision": {"action": "final_answer", "arguments": {"output": {
            "files": [{"path": "suite.py", "content": "from pathlib import Path\nPath('behavior.txt').write_text('changed')\n",
                       "runner": ["python"], "cwd": ".", "timeout_seconds": 120}],
            "coverage": ["C1"], "support_files": [],
        }}}}),
    ]
    with endpoint(replies) as (base, _):
        service = make_service(tmp_path, base, ["/ok"], request_timeout=10, routing_time_budget=20)
        tester = IndependentTester(
            NTGLangChainLLM(client=LLMClient(service=service)), [], workspace
        )
        result = asyncio.run(tester.verify("Verify existing behavior", "Done"))
    assert result.status == "FAIL"
    assert result.evidence[0]["exit_code"] == 0  # Exit zero alone cannot establish PASS.
    assert "./behavior.txt" in result.evidence[0]["source_changes"]
    assert destination.read_text() == "original"


def test_live_saved_tests_allow_runtime_data_and_expected_error_exit(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "application.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "if sys.argv[1] == 'invalid':\n    print('Invalid input', file=sys.stderr)\n    sys.exit(1)\n"
        "Path('expenses.json').write_text('[]')\n"
    )
    suite = (
        "import subprocess\n"
        "from pathlib import Path\n"
        "ok = subprocess.run(['python', 'application.py', 'valid'])\nassert ok.returncode == 0\n"
        "bad = subprocess.run(['python', 'application.py', 'invalid'], capture_output=True, text=True)\n"
        "assert bad.returncode == 1\nassert 'Invalid input' in bad.stderr\n"
        "assert Path('expenses.json').exists()\nprint('ASSERTIONS_PASSED')\n"
    )
    replies = [
        action("write_test_file", path="suite.py", content=suite),
        action("run_test_file", path="suite.py", runner=["python"]),
        'Final Answer: {"status":"PASS","summary":"Positive and negative behavior asserted"}',
    ]
    with endpoint(replies) as (base, _):
        service = make_service(tmp_path, base, ["/ok"], request_timeout=10, routing_time_budget=20)
        tester = IndependentTester(
            NTGLangChainLLM(client=LLMClient(service=service), structured_actions=False), [], workspace
        )
        result = asyncio.run(
            tester.verify("Persist valid entries; reject invalid input with exit 1", "Done")
        )
    assert result.status == "PASS", result
    assert result.evidence[0]["exit_code"] == 0
    assert "./expenses.json" in result.evidence[0]["runtime_artifacts"]
    assert result.test_files and Path(result.test_files[0]).read_text() == suite


def test_live_harness_path_correction_and_repeated_runtime_writes(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "actual_program.py").write_text("print('actual behavior')\n")
    wrong = "from pathlib import Path\nPath('guessed_program.py').read_text()\n"
    corrected = (
        "import subprocess\nfrom pathlib import Path\n"
        "observed = subprocess.run(['python', str(Path.cwd() / 'actual_program.py')], capture_output=True, text=True)\n"
        "assert observed.returncode == 0\nassert observed.stdout.strip() == 'actual behavior'\n"
        "Path('runtime_data.json').write_text('runtime only')\nprint('ASSERTED')\n"
    )
    replies = [
        action("write_test_file", path="behavior_suite.py", content=wrong),
        action("run_test_file", path="behavior_suite.py", runner=["python"]),
        'Final Answer: {"status":"PASS","summary":"False claim"}',
        action("write_test_file", path="behavior_suite.py", content=corrected),
        action("run_test_file", path="behavior_suite.py", runner=["python"]),
        action("run_test_file", path="behavior_suite.py", runner=["python"]),
        'Final Answer: {"status":"PASS","summary":"Behavior asserted twice"}',
    ]
    with endpoint(replies) as (base, calls):
        service = make_service(tmp_path, base, ["/ok"], request_timeout=10, routing_time_budget=20)
        tester = IndependentTester(
            NTGLangChainLLM(client=LLMClient(service=service), structured_actions=False), [], workspace
        )
        result = asyncio.run(tester.verify("Verify actual program behavior", "Completed"))
        assert any(
            '"project_files": ["actual_program.py"]' in body["messages"][-1]["content"]
            for _, body in calls
        )
    assert result.status == "PASS", result
    assert len(result.evidence) == 3
    assert result.evidence[0]["exit_code"] == 1
    assert "FileNotFoundError" in result.evidence[0]["stderr"]
    assert result.evidence[0]["superseded"]
    assert result.evidence[-1]["exit_code"] == 0
    assert not result.evidence[-1]["source_changes"]
    assert not (workspace / "runtime_data.json").exists()
