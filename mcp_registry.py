import os
from pathlib import Path

from langchain.mcp import MCPAdapter


WORKSPACE = r"C:\Users\perur\Desktop\workspace"


async def get_mcp_tools():

    shell_env = os.environ.copy()

    shell_env.update(
        {
            "ALLOW_COMMANDS": os.environ.get("NTG_SHELL_ALLOW_COMMANDS", "git,pytest,whoami"),
            "MCP_SHELL_DEFAULT_TIMEOUT_SECONDS": "60",
            "MCP_SHELL_MAX_TIMEOUT_SECONDS": "300",
            "MCP_SHELL_CHILD_ENV_ALLOWLIST": "TEMP,TMP,USERPROFILE,APPDATA,LOCALAPPDATA",
        }
    )

    shell_python = Path(__file__).resolve().parent / ".venv-shell" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    if not shell_python.is_file():
        raise RuntimeError("Shell MCP is not installed. Run python setup_shell_mcp.py first.")
    for key in list(shell_env):
        if key.lower() == "path":
            del shell_env[key]
    shell_env["PATH"] = str(shell_python.parent) + os.pathsep + os.environ.get("PATH", "")

    adapter = MCPAdapter(
        {
            "filesystem": {
                "transport": "stdio",
                "command": "cmd",
                "args": [
                    "/c",
                    "npx",
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    WORKSPACE,
                ],
            },

            "shell": {
                "transport": "stdio",
                "command": str(shell_python),
                "args": ["-c", "from mcp_shell_server import main; main()"],
                "cwd": WORKSPACE,
                "env": shell_env,
            },
        }
    )

    tools = await adapter.list_tools()
    if not any(tool.name == "shell_shell_execute" for tool in tools):
        raise RuntimeError("Shell MCP failed to expose its execute tool. Check server stderr.")

    return adapter, tools
