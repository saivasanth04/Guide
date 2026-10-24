import asyncio
import os
import json
import sys
from pathlib import Path
from e2b_sandbox import E2BSandboxExecutor
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
Complete the user's task accurately and efficiently using the available context and tools.
Adapt your approach to the task, preserve relevant existing behavior, and make focused,
maintainable changes. Act directly when enough information is available; investigate
only what is needed to resolve uncertainty or verify the result.

Available tools and their input schemas:

{tools}

Available tool names:

{tool_names}

Workspace: {workspace}

Working principles:

- Select tools by their declared capabilities and follow their input schemas.
- Keep persistent project changes within the allowed workspace. Follow each tool's
  path conventions and execution boundaries; local and isolated environments may
  have different paths, files, dependencies, and lifetimes.
- Use tool descriptions and observations to establish what an environment contains
  and whether changes persist or synchronize. Do not assume changes in one
  environment are available in another.
- Choose inspection, editing, execution, and verification appropriate to the task.
  Use observed failures to guide corrections and avoid redundant calls.
- Base claims on actual observations. Report meaningful limitations or incomplete
  verification, and finish once the requested work is complete.

Response protocol:

- Return exactly one tool action or one final answer per response.
- For an action, replace the tool name and empty object below with the selected
  tool and its actual JSON arguments:

Action: tool_name
Action Input: {{}}

- Action Input must be a JSON object matching the selected tool's schema.
- Do not wrap arguments in Markdown fences or invent Observation text.
- Wait for the real tool observation before choosing the next action.
- When finished, use:

Final Answer: outcome, relevant verification, and any remaining limitations

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

    # Discover filesystem tools from MCP
    filesystem_tools = await mcp_client.get_tools()

    # E2B execution capability
    sandbox_executor = E2BSandboxExecutor(
        workspace=workspace,
    )

    try:
        sandbox_tool = sandbox_executor.as_tool()

        tools = [
            *filesystem_tools,
            sandbox_tool,
        ]

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
                "input": """
Run a live integration check of filesystem editing and sandbox execution.

Create a new, uniquely named test directory within the workspace so existing
files are preserved. Use the filesystem tools to create a Python script there
that defines factorial(n), rejects negative integers, and checks factorial(0) == 1
and factorial(5) == 120 when run. On success, print E2B_CHECK_V1_OK.

Run that script with execute_in_sandbox, using its relative project path.
In the same sandbox command, create a uniquely named marker under /tmp and print
its path. Then use the local filesystem tools to change only the script's success
message to E2B_CHECK_V2_OK. Execute the updated script in a second sandbox call
and verify that the marker created by the first call still exists.

Keep the test files locally so I can inspect them. Do not modify unrelated files.
If any step fails, report the actual failure rather than claiming success.
In the final answer, include the local script path, both commands' exit codes and
observed output, whether both calls used the same sandbox_id, and whether the
marker persisted. Do not include credentials or environment-file contents.
"""
            }
        )

        print("\nFinal Result:")
        print(result["output"])

    finally:
        await asyncio.to_thread(sandbox_executor.close)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except LLMError as error:
        print(json.dumps(error.as_dict(), indent=2), file=sys.stderr)
        raise SystemExit(1) from None
