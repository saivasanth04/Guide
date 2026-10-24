"""Local regressions and an opt-in live MCP/E2B integration check."""

import asyncio
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from e2b_sandbox import E2BSandboxExecutor
from ntg_llm.react_parser import parse_react_output
from e2b import CommandExitException


def test_workspace_must_be_directory(tmp_path):
    file = tmp_path / "file"
    file.write_text("data")
    with pytest.raises(ValueError):
        E2BSandboxExecutor(file)


def test_missing_key_returns_tool_observation(tmp_path, monkeypatch):
    executor = E2BSandboxExecutor(tmp_path)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    result = json.loads(asyncio.run(executor.as_tool().ainvoke({"command": "true"})))
    assert not result["success"]
    assert result["exit_code"] is None
    assert "E2B_API_KEY is missing" in result["error"]
    assert executor._sandbox is None


def test_workspace_excludes_credentials_dependencies_and_large_files(tmp_path):
    (tmp_path / "source.py").write_bytes(b"print('ok')")
    (tmp_path / "asset.bin").write_bytes(b"\x00\xff")
    (tmp_path / ".env.private").write_text("SECRET=example")
    dependencies = tmp_path / "node_modules"
    dependencies.mkdir()
    (dependencies / "dependency.js").write_text("ignored")
    with (tmp_path / "large.bin").open("wb") as file:
        file.truncate(E2BSandboxExecutor.MAX_FILE_SIZE + 1)
    executor = E2BSandboxExecutor(tmp_path)
    assert {path.name for path in executor._workspace_files()} == {"source.py", "asset.bin"}


def dependency_executor(workspace):
    executor = E2BSandboxExecutor(workspace)
    calls = []

    def run(command, **options):
        calls.append((command, options))
        return SimpleNamespace(exit_code=0, stdout="ok", stderr="")

    executor._sandbox = SimpleNamespace(
        sandbox_id="test",
        commands=SimpleNamespace(run=run),
        files=SimpleNamespace(write=lambda *_: None),
    )
    return executor, calls


def test_install_precedes_execution_and_tracks_requirement_changes(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("example==1\n")
    source = tmp_path / "app.py"
    source.write_text("print(1)")
    executor, calls = dependency_executor(tmp_path)
    assert json.loads(executor.execute("python app.py"))["success"]
    assert [command for command, _ in calls] == [
        "python -m pip install -r requirements.txt",
        "python app.py",
    ]
    calls.clear()
    source.write_text("print(2)")
    executor.execute("python app.py")
    assert [command for command, _ in calls] == ["python app.py"]
    calls.clear()
    requirements.write_text("example==2\n")
    executor.execute("python app.py")
    assert calls[0][0] == "python -m pip install -r requirements.txt"


@pytest.mark.parametrize(
    "lockfile, expected",
    [(None, "npm install"), ("package-lock.json", "npm ci"), ("npm-shrinkwrap.json", "npm ci")],
)
def test_nested_npm_install_uses_project_directory(tmp_path, lockfile, expected):
    project = tmp_path / "web"
    project.mkdir()
    (project / "package.json").write_text('{"name":"web"}')
    if lockfile:
        (project / lockfile).write_text("{}")
    executor, calls = dependency_executor(tmp_path)
    executor.execute("node web/app.js")
    assert calls[0][0] == expected
    assert calls[0][1]["cwd"] == executor.remote_workspace + "/web"


@pytest.mark.parametrize("section, installs", [("[project]", True), ("[tool.ruff]", False)])
def test_pyproject_only_installs_python_projects(tmp_path, section, installs):
    (tmp_path / "pyproject.toml").write_text(section + "\n")
    executor, calls = dependency_executor(tmp_path)
    executor.execute("python app.py")
    assert (calls[0][0] == "python -m pip install -e .") is installs


def test_install_failure_blocks_command_and_is_retried(tmp_path):
    (tmp_path / "requirements.txt").write_text("example==1\n")
    executor, calls = dependency_executor(tmp_path)

    def fail(command, **options):
        calls.append(command)
        raise CommandExitException(stderr="install failed", stdout="", exit_code=1, error=None)

    executor._sandbox.commands.run = fail
    for _ in range(2):
        result = json.loads(executor.execute("python app.py"))
        assert not result["success"]
        assert result["stage"] == "dependency_installation"
        assert result["stderr"] == "install failed"
    assert calls == ["python -m pip install -r requirements.txt"] * 2
    assert not executor._dependency_state


@pytest.mark.skipif(
    os.getenv("NTG_LIVE_E2B") != "1" or not shutil.which("npx"),
    reason="Set NTG_LIVE_E2B=1 with E2B credentials and npx for live verification",
)
def test_live_mcp_file_is_executed_in_e2b_and_sandbox_is_closed(tmp_path):
    from coder_agent import filesystem_config
    from langchain_mcp_adapters.client import MultiServerMCPClient

    async def run():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        tools = await MultiServerMCPClient(filesystem_config(str(workspace))).get_tools()
        executor = E2BSandboxExecutor(workspace)
        tools.append(executor.as_tool())
        by_name = {tool.name: tool for tool in tools}
        sandbox = None
        try:
            destination = workspace / "live_check.py"
            written = await by_name["write_file"].ainvoke(
                {
                    "path": str(destination),
                    "content": "assert sum([2, 3]) == 5\nprint('LIVE_MCP_E2B_OK')\n",
                }
            )
            assert destination.is_file(), written
            action = parse_react_output(
                "Action: execute_in_sandbox\nAction Input: "
                + json.dumps({"command": "python live_check.py"})
            )
            result = json.loads(await by_name[action.tool].ainvoke(action.tool_input))
            assert result["success"], result
            assert result["stdout"].strip() == "LIVE_MCP_E2B_OK"
            sandbox = executor._sandbox
        finally:
            await asyncio.to_thread(executor.close)
        assert sandbox is not None and not sandbox.is_running()

    asyncio.run(run())
