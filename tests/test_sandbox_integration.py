"""Real Docker checks; required lane fails rather than skipping absent infrastructure."""

import pytest

from ntg_llm import LLMClient
from ntg_llm.project_workflow import run_verified_project
from ntg_llm.sandbox import DockerSandboxRunner
from ntg_llm.testing_models import TestProfile
from tests.test_tool_agent import action, final, stack


@pytest.mark.parametrize(
    "source,outcome",
    [
        ("print('actual execution')", "passed"),
        ("raise NameError('actual failure')", "failed"),
        ("while True: pass", "timeout"),
        ("import os\nwhile True: os.write(1,b'x'*4096)", "output_limit"),
        ("open('/workspace/forbidden','w').write('bad')", "failed"),
        ("import socket\nsocket.create_connection(('1.1.1.1',443),timeout=1)", "failed"),
        ("data = bytearray(1024 * 1024 * 1024)", "resource_limit"),
        (
            "import subprocess\nsubprocess.Popen(['python','-c','import time; time.sleep(1000)'])\nwhile True: pass",
            "timeout",
        ),
    ],
)
def test_real_container_controls(tmp_path, sandbox_policy, source, outcome):
    (tmp_path / "app.py").write_text(source)
    policy = sandbox_policy.model_copy(update={"timeout_seconds": 3, "output_bytes": 1024})
    runner = DockerSandboxRunner(policy)
    report = runner.run(tmp_path, TestProfile(kind="script", entrypoint="app.py"))
    assert report.outcome == outcome
    assert report.cleanup_confirmed
    assert len(report.stdout.encode()) <= 1024
    assert not (tmp_path / "forbidden").exists()


def test_real_sandbox_failure_tester_repair_success(tmp_path, sandbox_policy):
    root, service, _ = stack(
        tmp_path,
        [
            action(
                "write_file",
                path="calculator.py",
                content="def add(a,b): return missing(a,b)",
                overwrite=False,
            ),
            final(),
            {
                "summary": "Undefined missing function",
                "evidence_fields": ["stderr"],
                "suggested_changes": ["Return a+b"],
            },
            action(
                "write_file",
                path="calculator.py",
                content="def add(a,b): return a+b",
                overwrite=True,
            ),
            final(),
        ],
    )
    tests = tmp_path / "trusted-tests"
    tests.mkdir()
    test_source = "import unittest\nfrom calculator import add\nclass Check(unittest.TestCase):\n def test_sum(self): self.assertEqual(add(2,3),5)\n"
    (tests / "test_calculator.py").write_text(test_source)
    result = run_verified_project(
        LLMClient(service=service),
        "Implement addition",
        workspace=root,
        policy=sandbox_policy,
        profile=TestProfile(kind="unittest", tests_root=str(tests)),
        allow_overwrite=True,
    )
    assert result.status == "passed" and result.repairs_used == 1
    assert "NameError" in result.reports[0].stderr
    assert result.reports[-1].tests_run == 1
    assert result.diagnostics["tester"][0]["source"] == "model"
    assert (tests / "test_calculator.py").read_text() == test_source


def test_empty_suite_cannot_pass(tmp_path, sandbox_policy):
    root, tests = tmp_path / "project", tmp_path / "tests"
    root.mkdir()
    tests.mkdir()
    report = DockerSandboxRunner(sandbox_policy).run(
        root, TestProfile(kind="unittest", tests_root=str(tests)), tests
    )
    assert report.outcome == "no_tests" and report.tests_run == 0


def test_cli_verified_run_executes_actual_written_script(
    tmp_path, sandbox_policy, monkeypatch, capsys
):
    import json
    import main

    root, service, _ = stack(
        tmp_path,
        [
            action(
                "write_file",
                path="check.py",
                content="assert 2+2 == 4\nprint('verified')",
                overwrite=False,
            ),
            final(),
        ],
    )
    monkeypatch.setattr(main, "LLMClient", lambda: LLMClient(service=service))
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "Write check.py",
            "--verify",
            "--workspace",
            str(root),
            "--entrypoint",
            ".\\check.py",
            "--sandbox-image",
            sandbox_policy.image_id,
            "--max-repair-attempts",
            "0",
        ],
    )
    main.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "passed" and result["repairs_used"] == 0
    assert result["reports"][0]["stdout"].strip() == "verified"
