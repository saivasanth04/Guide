"""Run the application entry point entirely against fake providers."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from ntg_llm import LLMClient, LLMError
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore
from tests.helpers import FakeRouter, deployments


@pytest.mark.parametrize("context", [None, "Use only standard-library Python."])
def test_main_prints_one_coder_result(tmp_path, monkeypatch, capsys, context):
    import ntg_llm

    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.messages = kwargs["messages"]
            return super().completion(**kwargs)

    service = CompletionService(
        deployments(), RotationStore(tmp_path / "state.db"), RecordingRouter
    )
    service.router.content = (
        '{"code":"def square(n): return n * n", "explanation":"Square a number."}'
    )
    client = LLMClient(service=service)
    monkeypatch.setattr(ntg_llm, "LLMClient", lambda: client)
    args = ["main.py", "Write a square function"]
    if context is not None:
        args.extend(["--context", context])
    monkeypatch.setattr("sys.argv", args)
    runpy.run_path(str(Path(main.__file__)), run_name="__main__")
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert captured.err == ""
    assert result["agent_name"] == "coder"
    assert result["output"] == {
        "code": "def square(n): return n * n",
        "explanation": "Square a number.",
    }
    assert result["diagnostics"]["attempt_count"] == 1
    assert result["diagnostics"]["failed_attempts"] == []
    assert json.loads(service.router.messages[1]["content"]) == {
        "task": "Write a square function",
        "context": context,
    }
    assert len(service.router.calls) == 1


def test_main_formats_provider_error_without_traceback(monkeypatch, capsys):
    def exhausted(**kwargs):
        raise LLMError(
            429, "Provider Retry-After is still active", {"provider": "gemini"}, retry_after=12.2
        )

    monkeypatch.setattr(
        main,
        "LLMClient",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=exhausted)),
        ),
    )
    monkeypatch.setattr("sys.argv", ["main.py", "private task", "--context", "private context"])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert captured.out == ""
    assert payload["status"] == 429
    assert payload["retry_after"] == 13
    assert payload["diagnostics"] == {"provider": "gemini", "agent_name": "coder"}
    assert "Traceback" not in captured.err
    assert "private" not in captured.err


@pytest.mark.parametrize("args,status", [([], 2), (["--help"], 0)])
def test_cli_usage_does_not_construct_a_client(monkeypatch, capsys, args, status):
    def unexpected_client():
        pytest.fail("Usage/help must not initialize a client")

    monkeypatch.setattr(main, "LLMClient", unexpected_client)
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == status
    output = capsys.readouterr()
    assert "usage:" in output.out + output.err


@pytest.mark.parametrize("allowed", [False, True])
def test_cli_workspace_and_overwrite_policy(tmp_path, monkeypatch, capsys, allowed):
    from tests.test_tool_agent import action, final

    class ScriptedRouter(FakeRouter):
        def completion(self, **kwargs):
            self.content = json.dumps(
                action("write_file", path="module.py", content="print(2)", overwrite=True)
                if not self.calls
                else final()
            )
            return super().completion(**kwargs)

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "module.py").write_text("original", encoding="utf-8")
    service = CompletionService(deployments(), RotationStore(tmp_path / "state.db"), ScriptedRouter)
    monkeypatch.setattr(main, "LLMClient", lambda: LLMClient(service=service))
    args = ["main.py", "Update the file", "--workspace", str(root)]
    if allowed:
        args.append("--allow-overwrite")
    monkeypatch.setattr("sys.argv", args)
    main.main()
    output = json.loads(capsys.readouterr().out)
    assert output["diagnostics"]["tool_calls"][0]["ok"] is allowed
    assert (root / "module.py").read_text() == ("print(2)" if allowed else "original")


@pytest.mark.parametrize(
    "flags,status",
    [
        (["--allow-overwrite"], 2),
        (["--max-tool-calls", "0"], 1),
        (["--max-tool-calls", "bad"], 2),
        (["--workspace", ""], 1),
    ],
)
def test_invalid_tool_options_never_construct_client(monkeypatch, capsys, flags, status):
    def unexpected():
        pytest.fail("Invalid configuration must not initialize a client")

    monkeypatch.setattr(main, "LLMClient", unexpected)
    monkeypatch.setattr("sys.argv", ["main.py", "Task", *flags])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == status
    if status == 1:
        assert json.loads(capsys.readouterr().err)["status"] == 400


def test_cli_missing_workspace_never_constructs_client(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        main, "LLMClient", lambda: pytest.fail("Invalid root must not initialize a client")
    )
    monkeypatch.setattr("sys.argv", ["main.py", "Task", "--workspace", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().err)["status"] == 400


def test_cli_failure_after_write_reports_partial_effects(tmp_path, monkeypatch, capsys):
    from tests.test_tool_agent import action

    class Limited(Exception):
        status_code = 429
        headers = {"Retry-After": "12"}

    class PartialRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Limited("mock-secret") if self.calls else None
            self.content = json.dumps(
                action("write_file", path="one", content="private", overwrite=False)
            )
            return super().completion(**kwargs)

    root = tmp_path / "workspace"
    root.mkdir()
    service = CompletionService(deployments(), RotationStore(tmp_path / "state.db"), PartialRouter)
    monkeypatch.setattr(main, "LLMClient", lambda: LLMClient(service=service))
    monkeypatch.setattr("sys.argv", ["main.py", "Task", "--workspace", str(root)])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    payload = json.loads(output.err)
    assert payload["status"] == 429 and payload["retry_after"] == 12
    assert payload["diagnostics"]["tool_calls"][0]["path"] == "one"
    assert (root / "one").read_text() == "private"
    assert "private" not in output.err and "mock-secret" not in output.err
