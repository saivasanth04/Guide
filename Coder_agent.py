import asyncio
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from mcp_registry import get_mcp_tools


WORKSPACE_DIR = r"C:\Users\perur\Desktop\workspace"

ROLE = """
You are an expert Python Software Engineer. You write clean, modular, and maintainable Python code strictly tailored to user requirements.
"""

INSTRUCTIONS = f"""
1. Review the user prompt and determine the required source code file name and logic.
2. Use the workspace filesystem tools to write the complete implementation into the root directory: {WORKSPACE_DIR}.
3. Include proper type hints, comprehensive docstrings, and explicit input validation (raising ValueError or TypeError where applicable).
4. Conclude with a concise summary confirming the exact file path created.
"""

CONTEXT = f"""
- Target workspace directory: {WORKSPACE_DIR}
- Do not place unit tests here; this agent only generates production/source code.
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


async def main():
    result = await coder_agent(
        """
Create factorial.py containing a function `calculate_factorial(n: int) -> int`.
Requirements:
- Validate that `n` is an integer (raise TypeError if not).
- Validate that `n >= 0` (raise ValueError if negative).
- Return 1 for n=0 and n=1.
- Accurately calculate the factorial for positive integers.
Save the file directly in the workspace root.
"""
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())