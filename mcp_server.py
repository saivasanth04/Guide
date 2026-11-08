"""Expose GoModel's aggregated MCP tools for the lifetime of one agent run."""

from contextlib import asynccontextmanager

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools


@asynccontextmanager
async def gateway_tools(base_url, api_key=None):
    url = base_url.rstrip("/").removesuffix("/v1") + "/mcp"
    client = MultiServerMCPClient({"gomodel": {
        "transport": "streamable_http",
        "url": url,
        "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
    }})
    async with client.session("gomodel") as session:
        yield await load_mcp_tools(session)
