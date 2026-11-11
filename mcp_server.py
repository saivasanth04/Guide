"""Expose GoModel's aggregated MCP tools for the lifetime of one agent run."""

from contextlib import asynccontextmanager

from mcp.types import TextContent
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
        tools = await load_mcp_tools(session, tool_interceptors=[report_tool])
        print(f"[Loaded MCP Tools from {url}]: {[tool.name for tool in tools]}", flush=True)
        if not tools:
            raise RuntimeError(f"{url} returned no tools. Check GoModel's config.yaml, caller access, and upstream initialization logs.")
        yield tools


async def report_tool(request, handler):
    result = await handler(request)
    print(f"[MCP {'error' if result.isError else 'success'}] {request.name}", flush=True)
    if result.isError:
        print(result.content, flush=True)
    # The chat route rejects empty text blocks (e.g. an empty directory listing).
    blocks = [b for b in result.content if not isinstance(b, TextContent) or b.text]
    if len(blocks) != len(result.content):
        return result.model_copy(update={"content": blocks or [TextContent(type="text", text="(No content.)")]})
    return result
