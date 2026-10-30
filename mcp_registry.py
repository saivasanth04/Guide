from langchain.mcp import MCPAdapter


WORKSPACE = r"C:\Users\perur\Desktop\workspace"


async def get_mcp_tools():
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
                "command": "cmd",
                "args": [
                    "/c",
                    "go",
                    "run",
                    "github.com/inercia/MCPShell@v0.1.8",
                    "mcp",
                    "--tools",
                    MCPSHELL_CONFIG,
                ],
            },
        }
    )

    tools = await adapter.list_tools()

    return adapter, tools