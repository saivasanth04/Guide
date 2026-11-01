# Planner_agent.py

import asyncio

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from mcp_registry import get_mcp_tools


WORKSPACE_DIR = r"C:\Users\perur\Desktop\workspace"

PLANNER_TOOLS = {
    "read_file",
    "read_multiple_files",
    "list_directory",
    "get_file_info",
    "start_search",
    "get_more_search_results",
    "stop_search",
    "list_searches",
}

SYSTEM_PROMPT = f"""
You are an independent Software Planning Agent.

Your responsibility is to understand the user's request, inspect the existing
project when relevant, and produce a precise implementation plan.

Workspace:
{WORKSPACE_DIR}

Rules:
- Inspect the workspace before planning changes to an existing project.
- Understand the current architecture and implementation first.
- Determine task-specific requirements, technologies, and approach from the request,
  supplied orchestrator context and prior outputs, workspace evidence, and available tools.
- Do not write, modify, rename, or delete files.
- Do not execute implementation or testing commands.
- Do not call the Coder, Tester, or Reviewer agents.
- Do not invent files, APIs, architecture, or existing behavior.
- Prefer the smallest correct change.
- Avoid unnecessary abstractions or additional code.
- Identify dependencies between implementation steps.
- Clearly distinguish existing behavior from required changes.

Return:

GOAL:
<what must be achieved>

CURRENT STATE:
<relevant existing implementation>

CHANGE SCOPE:
<files/components likely affected>

IMPLEMENTATION PLAN:
1. <ordered implementation step>
2. <next step>

VALIDATION:
<how the completed implementation should be verified>

RISKS:
<important risks or constraints, or "None">

DONE WHEN:
<clear completion criteria>
"""


async def planner_agent(user_request: str) -> str:
    adapter, tools = await get_mcp_tools()

    try:
        tools = [
            tool
            for tool in tools
            if getattr(tool, "name", None) in PLANNER_TOOLS
        ]

        model = ChatOpenAI(
            model="router",
            base_url="http://localhost:8080/v1",
            api_key="dummy",
        )

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
        )

        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": user_request,
                    }
                ]
            }
        )

        return result["messages"][-1].content

    finally:
        await adapter.__aexit__(None, None, None)

task = """
Inspect the workspace and plan the requested changes using the available project
requirements. If the intended goal is missing or ambiguous, identify what needs clarification.

Requirements:
- Inspect the existing files and architecture first.
- Identify exactly which files need changes.
- Explain the current behavior.
- Plan the minimum required code changes.
- Do not modify any files.
- Include how the implementation should be tested and validated.
"""

async def main():
    result = await planner_agent(task)
    print(result)
print(asyncio.run(main()))