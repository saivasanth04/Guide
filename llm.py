from openai import OpenAI
from pydantic import BaseModel
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from mcp_registry import get_mcp_tools

import asyncio
import json


class ChatResponse(BaseModel):
    answer: str
    summary: str
    confidence: float


ROLE = """
You are a Python Coding Agent.
"""


INSTRUCTIONS = """
Understand the user's request.
Use available tools whenever required.
Provide correct and concise Python solutions.
Do not add unnecessary code.
"""


CONTEXT = """
The user is learning Python and prefers simple,
easy-to-understand explanations.
"""


async def coder_agent(user_request: str):

    adapter, tools = await get_mcp_tools()

    try:
        model = ChatOpenAI(
            model="router",
            base_url="http://localhost:8080/v1",
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
            {
                "messages": [
                    {
                        "role": "user",
                        "content": user_request,
                    }
                ]
            }
        )

        final_message = result["messages"][-1]

        return final_message.content

    finally:
        await adapter.__aexit__(
            None,
            None,
            None,
        )


async def main():

    result = await coder_agent(
        """
Create factorial.py containing a Python function
that calculates the factorial of a number.
"""
    )

    print(result)


if __name__ == "__main__":
    asyncio.run(main())