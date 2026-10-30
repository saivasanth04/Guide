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
            }
        }
    )

    tools = await adapter.list_tools()

    return adapter, tools