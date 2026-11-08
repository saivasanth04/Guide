"""Run the planner skill with tools supplied by MCP servers."""

import asyncio
import os
from uuid import uuid4

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from mcp_server import gateway_tools
from skills import SkillManager

WORK_DIR = r"C:\Users\perur\Desktop\workspace"
TASK_GOAL = "Create a phased implementation plan for adding dynamic rate-limiting to our API."


async def main(task):
    os.chdir(WORK_DIR)
    planner_skill = SkillManager().load_skill("planner")
    base_url = os.environ.get("GOMODEL_BASE_URL", "http://localhost:8080/v1")
    key = os.environ.get("GOMODEL_MASTER_KEY")
    async with gateway_tools(base_url, key) as tools:
        await run_agent(task, tools, planner_skill, base_url, key)


async def run_agent(task, tools, planner_skill, base_url, key):
    session_id = f"planner-{uuid4()}"
    model = init_chat_model(
        model=os.environ.get("GOMODEL_MODEL", "router"),
        model_provider="openai",
        base_url=base_url,
        api_key=key or "dummy-key",
        default_headers={"X-Session-ID": session_id, "x-session-affinity": session_id},
        temperature=0.2,
        timeout=120,
        max_retries=1,
    )
    system_prompt = (
        "You are a software planning agent. Create and validate plans without implementing them. "
        "Follow the skill below. Use relevant tools to ground the plan in the user's context. "
        "Avoid accessing secrets and identify assumptions or missing evidence.\n\n"
        f"INSTRUCTIONS FROM SKILL.MD:\n{planner_skill['instructions']}"
    )
    agent = create_agent(model=model, tools=tools, system_prompt=system_prompt)
    print(f"[Loaded MCP Tools]: {[tool.name for tool in tools]}", flush=True)
    print(f"[Starting Agent Task]: {task}\n", flush=True)
    response = await agent.ainvoke(
        {"messages": [("user", f"Working directory: {WORK_DIR}\n\n{task}")]}, {"recursion_limit": 30}
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
    asyncio.run(main(TASK_GOAL))
