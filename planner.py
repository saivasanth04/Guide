"""Run the planner skill with tools supplied by MCP servers."""

import asyncio
import os
import re
from pathlib import Path
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain.chat_models import init_chat_model
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

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
        "You are a software planning agent. Create plans without implementing them. "
        "Follow the skill below. Use relevant tools to ground the plan in the user's context. "
        "Avoid accessing secrets and identify assumptions or missing evidence.\n\n"
        f"INSTRUCTIONS FROM SKILL.MD:\n{planner_skill['instructions']}"
    )
    def clarify(questions: str) -> str:
        """Ask the user a batch of essential questions and wait for their answers."""
        return interrupt(questions)

    saved = {}

    def save_plan(filename: str, markdown: str) -> str:
        """Save or revise the finished Markdown plan in the working directory."""
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-plan\.md", filename) or not markdown.strip():
            raise ValueError("Supply a kebab-case -plan.md filename and nonempty Markdown.")
        path = Path(WORK_DIR) / filename
        if path.exists() and path not in saved:
            path = path.with_name(f"{path.stem.removesuffix('-plan')}-{session_id[-8:]}-plan.md")
        with path.open("w" if path in saved else "x", encoding="utf-8") as output:
            output.write(markdown)
        saved[path] = markdown
        return str(path)

    names = {tool.name for tool in tools}

    @wrap_tool_call
    async def report_tool(request, handler):
        result = await handler(request)
        if isinstance(result, ToolMessage) and request.tool_call["name"] in names:
            print(f"[MCP {result.status}] {request.tool_call['name']}", flush=True)
            if result.status == "error":
                print(result.content, flush=True)
            if isinstance(result.content, list):
                blocks = [b for b in result.content if not (
                    isinstance(b, dict) and b.get("type") == "text" and not b.get("text")
                )]
                result = result.model_copy(update={"content": blocks or "(Tool returned no content.)"})
        return result

    agent = create_agent(
        model=model, tools=[*tools, clarify, save_plan], checkpointer=InMemorySaver(), middleware=[report_tool],
        system_prompt=system_prompt + "\nUse MCP tools to investigate the workspace. "
        "Use clarify for essential questions and save_plan for all plan writes. "
        "Report failed or unavailable research.",
    )
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 80}
    request = {"messages": [("user", f"Working directory: {WORK_DIR}\n\n{task}")]}
    rounds = 0
    for _ in range(8):
        result = await agent.ainvoke(request, config)
        if pending := result.get("__interrupt__"):
            answers = {}
            for item in pending:
                if rounds >= 2:
                    answers[item.id] = "Clarification limit reached. Proceed with stated assumptions."
                    continue
                print(item.value, flush=True)
                answer = await asyncio.to_thread(input, "Answer (/cancel to exit): ")
                if answer.strip().lower() == "/cancel":
                    print("Planning cancelled.")
                    return
                answers[item.id] = answer
                rounds += 1
            request = Command(resume=answers)
            continue
        calls = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.name in names]
        if any(m.status == "success" for m in calls) and saved:
            path = next(reversed(saved))
            if path.read_text(encoding="utf-8") != saved[path]:
                raise RuntimeError(f"Saved plan failed verification: {path}")
            print(result["messages"][-1].content)
            print(f"Verified plan: {path}")
            return
        request = {"messages": [("user", "Continue the same task. Investigate with MCP and save the plan using save_plan. Use clarify if blocked on user input.")]}
    raise RuntimeError("Planner exhausted its continuation budget without completing the workflow.")


if __name__ == "__main__":
    asyncio.run(main(TASK_GOAL))
