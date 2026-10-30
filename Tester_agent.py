from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
import asyncio
from mcp_registry import get_mcp_tools


ROLE = """
You are a Software Tester Agent.
"""


INSTRUCTIONS = """
Test and validate the software in the workspace using the available tools.

- Inspect the project before testing.
- Detect the programming language, framework, build system, and test framework dynamically.
- Determine the correct commands required to build, compile, execute, lint, or test the project.
- Use the shell tool to execute those commands.
- The shell tool takes an argv array, for example ["pytest", "--version"], and a directory.
- Use the workspace C:\\Users\\perur\\Desktop\\workspace as the directory.
- The configured shell server blocks interpreter and shell launchers (including python).
- Prefer pytest for Python tests; report policy rejections without trying alternate executable names.
- Use filesystem tools when project inspection is required.
- Do not assume a specific programming language or framework.
- Do not modify or fix source code.
- Do not create unnecessary test files.
- Report failures exactly as observed.
- Distinguish application failures from missing dependencies, environment problems, or unavailable toolchains.
- Base the result on actual execution evidence whenever possible.
- Return a concise result containing the commands executed, success or failure, and relevant error evidence.
"""


async def tester_agent(user_request: str):

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
""",
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
        await adapter.__aexit__(
            None,
            None,
            None,
        )
async def main():

    result = await tester_agent(
        """
Test the factorial function in factorial.py.
"""
    )

    print(result)


if __name__ == "__main__":
    asyncio.run(main())
