import asyncio
import os
import json
import sys
from pathlib import Path

from langchain_classic.agents import (
    AgentExecutor,
    create_react_agent,
)
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)

from ntg_llm import LLMClient, LLMError
from ntg_llm.react_parser import parse_react_output, render_mcp_tools
from ntg_llm.langchain_adapter import NTGLangChainLLM


# --------------------------------------------------
# ReAct Prompt
# --------------------------------------------------

REACT_PROMPT = PromptTemplate.from_template(
"""
You are a general-purpose software engineering agent.
Your responsibility is to solve software development tasks accurately, efficiently, and autonomously using the context and tools available to you.
Adapt your approach to the task, make sound engineering decisions, preserve existing system behavior where appropriate, avoid unnecessary changes, and produce a correct, maintainable result.
Use available tools only when they are useful, and finish with a concise summary of the outcome.
Act directly when the task already supplies the target, content, and enough context.
Explore files or directories only to resolve information needed for the task;
avoid routine workspace discovery and redundant verification calls.
After the requested work and necessary verification succeed, return the final answer.

You have access to these filesystem tools:

{tools}

Available tool names:

{tool_names}

Workspace and tool protocol:

- Never modify files outside the allowed workspace: {workspace}
- Use absolute filesystem paths inside the workspace.
- Action Input must be a JSON object matching the selected tool's schema.
- Return one action per response and wait for the real tool observation.
- Do not wrap JSON in Markdown fences or output Observation yourself.

When you need a tool, use:

Action: tool_name
Action Input: {{"path": "absolute path inside the workspace"}}

When the task is fully complete, use:

Final Answer: short description of what was completed

Task:

{input}

{agent_scratchpad}
"""
)


# --------------------------------------------------
# MCP Filesystem
# --------------------------------------------------

def filesystem_config(workspace: str):

    if os.name == "nt":

        return {
            "filesystem": {
                "transport": "stdio",
                "command": "cmd",
                "args": [
                    "/c",
                    "npx",
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    workspace,
                ],
            }
        }

    return {
        "filesystem": {
            "transport": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                workspace,
            ],
        }
    }


# --------------------------------------------------
# Main
# --------------------------------------------------

async def main():

    # Restrict MCP to this project
    workspace = str(Path(r"C:\Users\perur\Desktop\workspace").resolve())

    # ----------------------------------------------
    # Your existing NTG LLM
    # ----------------------------------------------

    ntg_client = LLMClient()

    llm = NTGLangChainLLM(
        client=ntg_client,
        model_name="router",
    )

    # ----------------------------------------------
    # MCP Client
    # ----------------------------------------------

    mcp_client = MultiServerMCPClient(filesystem_config(workspace))

    # Discover tools from MCP
    tools = await mcp_client.get_tools()

    print("Available MCP tools:")

    for tool in tools:
        print(f" - {tool.name}")

    # ----------------------------------------------
    # Real ReAct Component
    # ----------------------------------------------

    react_agent = create_react_agent(
        llm=llm,
        tools=tools,
        prompt=REACT_PROMPT.partial(workspace=workspace),
        output_parser=RunnableLambda(parse_react_output),
        tools_renderer=render_mcp_tools,
        stop_sequence=True,
    )

    # ----------------------------------------------
    # ReAct Executor
    # ----------------------------------------------

    agent = AgentExecutor(
        agent=react_agent,
        tools=tools,
        verbose=True,
        max_iterations=25,
        handle_parsing_errors=True,
    )

    # ----------------------------------------------
    # Task
    # ----------------------------------------------

    result = await agent.ainvoke(
        {
            "input": """create a new file called "hello.txt" in the workspace and write "Hello, World!" into it. Then read the contents of the file and return it as the final answer.  """
        }
    )

    print("\nFinal Result:")
    print(result["output"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except LLMError as error:
        print(json.dumps(error.as_dict(), indent=2), file=sys.stderr)
        raise SystemExit(1) from None
