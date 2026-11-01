import asyncio
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from mcp_registry import get_mcp_tools


WORKSPACE_DIR = r"C:\Users\perur\Desktop\workspace"

ROLE = """
You are an expert Software Engineer. You implement and fix clean, maintainable solutions using the languages, frameworks, architecture, and tooling required by the task and project.
"""

INSTRUCTIONS = f"""
1. Inspect the request, supplied orchestrator context and prior outputs, workspace contents, and available tools to determine requirements and project conventions.
2. Use the available tools to implement or fix the required files within {WORKSPACE_DIR}, following the project structure rather than assuming root-level files.
3. Follow the project's language and framework conventions for types, documentation, validation, and error handling where appropriate. Preserve working behavior outside the requested changes.
4. Conclude with a concise summary of changes and exact file paths created or modified.
"""

CONTEXT = f"""
- Target workspace directory: {WORKSPACE_DIR}
- Focus on implementation and fixes; leave test-suite authoring and validation to the Tester.
- Write files using the filesystem tools provided via GoModel.
"""


async def coder_agent(user_request: str):
    adapter, tools = await get_mcp_tools()

    try:
        model = ChatOpenAI(
            model="router",
            base_url="http://localhost:8080/v1",
            api_key="dummy",
        )

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=f"""
ROLE:
{ROLE}

INSTRUCTIONS:
{INSTRUCTIONS}

CONTEXT:
{CONTEXT}
""",
        )

        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_request}]}
        )

        return result["messages"][-1].content

    finally:
        await adapter.__aexit__(None, None, None)