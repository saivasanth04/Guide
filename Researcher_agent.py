import asyncio

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from mcp_registry import get_mcp_tools


WORKSPACE_DIR = r"C:\Users\perur\Desktop\workspace"

SYSTEM_PROMPT = f"""
You are an independent Software Research Agent.

Your responsibility is to investigate technical questions and return accurate,
evidence-based findings that can help other agents make decisions.

Workspace:
{WORKSPACE_DIR}

Rules:
- Understand the research question before acting.
- Inspect the workspace when project context is relevant.
- Use web-search tools when external information is required.
- Prefer official documentation and primary sources.
- Compare alternatives when appropriate.
- Clearly distinguish verified facts from assumptions.
- Do not write, modify, rename, or delete project files.
- Do not implement solutions.
- Do not call Planner, Coder, Tester, or Reviewer agents.
- Do not invent libraries, APIs, features, or limitations.
- Keep research focused on the requested problem.

Return:

QUESTION:
<research question>

PROJECT CONTEXT:
<relevant workspace findings>

FINDINGS:
<verified findings>

OPTIONS:
<possible approaches and trade-offs>

RECOMMENDATION:
<best supported approach>

RISKS:
<important limitations or concerns>

SOURCES:
<sources used>
"""


async def researcher_agent(user_request: str) -> str:
    adapter, tools = await get_mcp_tools()

    try:
        # Keep web-search tools and safe read-only workspace tools.
        tools = [
            tool for tool in tools
            if any(
                name in getattr(tool, "name", "").lower()
                for name in (
                    "web-search",
                    "search",
                    "read_file",
                    "read_multiple_files",
                    "list_directory",
                    "get_file_info",
                )
            )
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
async def main():
    result = await researcher_agent(
        """
    Research the best approach for securely executing AI-generated
    Python, Java, C, and C++ code.

    Compare:
    - direct local execution
    - subprocess isolation
    - E2B
    - container-based sandboxing

    Consider security, portability, cost, resource isolation,
    and suitability for a multi-user coding-agent platform.

    Do not implement anything.
    Recommend the best architecture and provide sources.
    """
    )

    print(result)

if __name__ == "__main__":
    asyncio.run(main())