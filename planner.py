"""Run the planner skill with tools supplied by MCP servers."""

import asyncio
import os
import shutil
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import yaml
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from skills import SkillManager

PROJECT_DIR = Path(__file__).resolve().parent
TASK_GOAL = "Create a phased implementation plan for adding dynamic rate-limiting to our API."


def load_mcp_servers(path: Path = PROJECT_DIR / "mcp.yaml") -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("mcp_servers"), dict):
        raise ValueError(f"{path}: expected an mcp_servers mapping")
    servers = {}
    for name, settings in config["mcp_servers"].items():
        if not isinstance(settings, dict) or settings.get("transport") != "stdio":
            raise ValueError(f"MCP server {name}: expected transport: stdio")
        connection = settings.copy()
        command = connection.get("command")
        args = connection.get("args", [])
        if not isinstance(command, str) or not command:
            raise ValueError(f"MCP server {name}: command is required")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError(f"MCP server {name}: args must be a list of strings")
        executable = shutil.which(command + ".cmd" if os.name == "nt" and command == "npx" else command)
        if executable is None:
            raise FileNotFoundError(f"MCP server {name}: executable {command!r} is not on PATH")
        connection["command"] = executable
        connection["args"] = [arg.replace("${PROJECT_DIR}", str(PROJECT_DIR)) for arg in args]
        connection["cwd"] = str(PROJECT_DIR)
        servers[name] = connection
    return servers


async def main():
    planner_skill = SkillManager().load_skill("planner")
    client = MultiServerMCPClient(load_mcp_servers(), tool_name_prefix=True)
    # Keep sessions alive for the run: terminal process IDs and pagination
    # belong to a server process and cannot survive a restart per tool call.
    async with AsyncExitStack() as stack:
        tools = []
        for name in client.connections:
            session = await asyncio.wait_for(
                stack.enter_async_context(client.session(name)), timeout=120
            )
            tools.extend(await asyncio.wait_for(
                load_mcp_tools(session, server_name=name, tool_name_prefix=True), timeout=120
            ))
        await run_agent(tools, planner_skill)


async def run_agent(tools, planner_skill):
    session_id = f"planner-{uuid4()}"
    model = init_chat_model(
        model=os.environ.get("GOMODEL_MODEL", "router"),
        model_provider="openai",
        base_url=os.environ.get("GOMODEL_BASE_URL", "http://localhost:8080/v1"),
        api_key=os.environ.get("GOMODEL_MASTER_KEY") or "dummy-key",
        default_headers={"X-Session-ID": session_id, "x-session-affinity": session_id},
        temperature=0.2,
        timeout=120,
        max_retries=1,
    )
    system_prompt = (
        f"You are an agent executing the skill: '{planner_skill['name']}'.\n"
        f"Project directory: {PROJECT_DIR}\n"
        "Follow the skill's clarification phase when requirements are missing. "
        "Only create plans; do not implement changes. "
        "Do not read .env files, credentials, or private keys. "
        "Exclude .git, .venv, node_modules, caches, and lockfiles from codebase research. "
        "Start with README.md, planner.py, skills.py, and mcp.yaml; this project has no API server. "
        "If a requested documentation tool is unavailable, state that limitation.\n\n"
        f"INSTRUCTIONS FROM SKILL.MD:\n{planner_skill['instructions']}"
    )
    agent = create_agent(model=model, tools=tools, system_prompt=system_prompt)
    print(f"[Loaded MCP Tools]: {[tool.name for tool in tools]}", flush=True)
    print(f"[Starting Agent Task]: {TASK_GOAL}\n", flush=True)
    response = await agent.ainvoke(
        {"messages": [("user", TASK_GOAL)]}, {"recursion_limit": 30}
    )
    if not response["messages"][-1].content:
        response = await agent.ainvoke(
            {"messages": [*response["messages"], ("user", "Your response was empty. Please provide your findings or clarification questions in text.")]},
            {"recursion_limit": 30},
        )
    if not response["messages"][-1].content:
        raise RuntimeError("GoModel returned an empty final response twice; check the selected model and gateway logs")
    print("\n--- Final Planner Output ---")
    print(response["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
