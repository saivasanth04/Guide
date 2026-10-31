import os
from langchain.mcp import MCPAdapter

GOMODEL_MCP_URL = os.getenv("GOMODEL_MCP_URL", "http://localhost:8080/mcp")


async def get_mcp_tools():
    """Connects to GoModel's aggregated MCP Gateway."""
    adapter = MCPAdapter(
        {
            "gomodel": {
                "transport": "streamable-http",
                "url": GOMODEL_MCP_URL,
            }
        }
    )

    tools = await adapter.list_tools()
    return adapter, tools
