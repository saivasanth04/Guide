"""Local regressions and an opt-in live MCP/E2B integration check."""

import asyncio
import json
import os
import shutil

import pytest

from e2b_sandbox import E2BSandboxExecutor
from ntg_llm.react_parser import parse_react_output


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
