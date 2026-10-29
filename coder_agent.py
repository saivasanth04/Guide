import asyncio
import os
import json
import sys
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from dotenv import dotenv_values
from e2b_sandbox import E2BSandboxExecutor
from agent_orchestrator import repair_limit, run_workflow
from tester_agent import TesterAgent
from reviewer_agent import ReviewerAgent
from langchain_classic.agents import (
    AgentExecutor,
)
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)

from ntg_llm import LLMClient, LLMError
from ntg_llm.agent_protocol import build_react_agent, TEXT_RESPONSE_PROTOCOL
from agent_orchestrator import has_successful_execution
from ntg_llm.langchain_adapter import NTGLangChainLLM


# Edit these values, then run: python coder_agent.py
WORKSPACE = Path(r"C:\Users\perur\Desktop\workspace")
# Use False when TASK should change an existing project directly in WORKSPACE.
NEW_WORKSPACE_PER_RUN = True

TASK = """
Build a small command-line expense tracker in a new project directory within
this workspace. Choose a suitable language and project structure.

Users should be able to add an expense with an amount, category, and description;
list recorded expenses; and display a summary of total spending by category.
Store expenses in a local file so records persist between separate invocations.
Use exact decimal arithmetic for money and display amounts to two decimal places.
Reject invalid or nonpositive amounts with a useful error and a nonzero exit code,
without changing previously saved records. Reject amounts that round to zero;
never persist a zero-valued expense. An empty tracker should display a
helpful message and a zero total.

Include dependency declarations if needed and a README explaining setup,
available commands, and a short example session. Keep existing unrelated files
unchanged. Finish with the project location and instructions for using it.
"""


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
- Before declaring implementation complete, execute appropriate checks in the sandbox,
  inspect their actual output, and correct observed failures. Do not claim successful
  execution based only on reading source. Leave independent .agent_tests files unchanged.
- Track all requested deliverables, including documentation and setup. Verify happy
  paths, persistence/state transitions, invalid inputs and relevant boundaries.
  For numeric domains consider finite values, precision, rounding and domain limits.
  Expected failures must be asserted explicitly; do not hide exit codes with trailing
  print/echo commands. Keep documented commands consistent with the files you create.
  For requested input rejection, assert useful diagnostics as well as nonzero exit;
  an uncaught traceback is not controlled validation. If rejected operations must
  preserve state, seed valid existing state and compare it after rejection.

Response protocol:

{response_protocol}
Your final result must describe the implemented outcome, actual verification,
project location, usage instructions when requested, and remaining limitations.

Task:

{input}

{agent_scratchpad}
"""
).partial(response_protocol=TEXT_RESPONSE_PROTOCOL)


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


def _protect_independent_tests(tools, workspace):
    protected = (Path(workspace).resolve() / ".agent_tests").resolve()

    def wrap(original):
        async def invoke(**arguments):
            for key in ("path", "source", "destination"):
                if key not in arguments:
                    continue
                path = (Path(workspace) / arguments[key]).resolve()
                if path.is_relative_to(protected) or (
                    original.name in {"move_file", "delete_file"} and protected.is_relative_to(path)
                ):
                    return json.dumps({"success": False, "error": "Independent Tester files are read-only to the Coder; repair the implementation instead."})
            return await original.ainvoke(arguments)

        return StructuredTool(name=original.name, description=original.description,
                              args_schema=original.args_schema, coroutine=invoke)

    return [wrap(tool) if tool.name in {"write_file", "edit_file", "move_file", "create_directory", "delete_file"} else tool for tool in tools]


def create_coder_agent(llm, tools, workspace, *, verbose=True):
    """Build the existing fully capable Coder; verification is orchestrated separately."""
    tools = _protect_independent_tests(tools, workspace)
    react_agent = build_react_agent(
        llm, tools, REACT_PROMPT.partial(workspace=str(workspace)),
        finish_guard=lambda steps: has_successful_execution({"intermediate_steps": steps}),
    )
    return AgentExecutor(agent=react_agent, tools=tools, verbose=verbose,
                         max_iterations=25, handle_parsing_errors=True,
                         return_intermediate_steps=True)


# --------------------------------------------------
# Main
# --------------------------------------------------

async def main(workspace: str | Path | None = None, task: str | None = None,
               max_repair_attempts: int | None = None):

    # Restrict MCP to this project
    if workspace is None and NEW_WORKSPACE_PER_RUN:
        # A new default run must not accidentally reuse yesterday's generated project.
        workspace = WORKSPACE / "runs" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
        workspace.mkdir(parents=True)
    elif workspace is None:
        workspace = WORKSPACE
    workspace = str(Path(workspace).resolve())
    print(f"[Workflow] Workspace: {workspace}", flush=True)
    limit = repair_limit() if max_repair_attempts is None else max_repair_attempts

    # ----------------------------------------------
    # Your existing NTG LLM
    # ----------------------------------------------

    ntg_client = LLMClient()
    config = dotenv_values(Path(__file__).resolve().parent / ".env")
    def setting(name, default=None):
        return os.getenv(name, config.get(name, default)) or None

    llm = NTGLangChainLLM(
        client=ntg_client,
        model_name=setting("CODER_MODEL", "router"),
        trace_routing=True,
        agent_role="coder",
        structured_actions=True,
        reasoning_effort=setting("CODER_REASONING_EFFORT", setting("AGENT_REASONING_EFFORT")),
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

        agent = create_coder_agent(llm, tools, workspace)
        tester_llm = llm.model_copy(update={
            "agent_role": "tester",
            "model_name": setting("TESTER_MODEL", "router"),
            "reasoning_effort": setting("TESTER_REASONING_EFFORT", setting("AGENT_REASONING_EFFORT")),
        })
        tester = TesterAgent(tester_llm, filesystem_tools, workspace, verbose=True)
        reviewer_llm = llm.model_copy(update={
            "agent_role": "reviewer",
            "model_name": setting("REVIEWER_MODEL", "router"),
            "reasoning_effort": setting("REVIEWER_REASONING_EFFORT", setting("AGENT_REASONING_EFFORT")),
        })
        reviewer = ReviewerAgent(reviewer_llm, filesystem_tools, workspace, verbose=True)

        # ----------------------------------------------
        # Task
        # ----------------------------------------------

        task = TASK if task is None else task
        inventory = sandbox_executor.workspace_inventory()
        task += "\nSystem environment context at workflow start (not additional requirements): " + json.dumps({
            "workspace": workspace, "existing_project_files": inventory[:500],
            "inventory_truncated": len(inventory) > 500,
        }) + "\nUse this authoritative inventory to avoid redundant directory discovery."
        result = await run_workflow(task, agent, tester, reviewer, max_repair_attempts=limit)

        print("\nFinal Result:")
        print(result.model_dump_json(indent=2))
        return result

    finally:
        await asyncio.to_thread(sandbox_executor.close)


if __name__ == "__main__":
    try:
        outcome = asyncio.run(main())
        raise SystemExit(0 if outcome.status == "PASS" else 1)
    except LLMError as error:
        print(json.dumps(error.as_dict(), indent=2), file=sys.stderr)
        raise SystemExit(1) from None
