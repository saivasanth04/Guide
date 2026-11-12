import asyncio
import hashlib
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from typing_extensions import Annotated, TypedDict

from contracts import Blocked, DevelopmentPlan, ResearchRequest
from contracts import validate_plan as validate_plan
from mcp_server import gateway_tools
from skills import SkillManager, runtime_instructions
from tool_policy import WorkspaceGuard, select_tools, worker_policy
from tool_policy import ask_user as ask_user

WORK_DIR = Path(r"C:\Users\perur\Desktop\workspace")


class Plan(TypedDict):
    """Final validated planning artifact, including verification steps and remaining risks."""

    filename: Annotated[str, "A kebab-case filename ending in -plan.md"]
    markdown: Annotated[str, "The complete Markdown plan following the supplied skill"]


def create_model(thread):
    """Use the existing GoModel route and keep provider credentials in the gateway."""
    timeout = float(os.environ.get("GOMODEL_TIMEOUT", "300"))
    retries = int(os.environ.get("GOMODEL_MAX_RETRIES", "2"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("GOMODEL_TIMEOUT must be a positive, finite number of seconds.")
    if not 0 <= retries <= 5:
        raise ValueError("GOMODEL_MAX_RETRIES must be an integer between 0 and 5.")
    return init_chat_model(
        model=os.environ.get("GOMODEL_MODEL", "router"), model_provider="openai",
        base_url=os.environ.get("GOMODEL_BASE_URL", "http://localhost:8080/v1"),
        api_key=os.environ.get("GOMODEL_MASTER_KEY") or "dummy-key",
        timeout=timeout, max_retries=retries, temperature=0.2,
        default_headers={"X-Session-ID": thread, "x-session-affinity": thread},
    )


def create_planner(model, tools, *, checkpointer=None, response_schema=None,
                   middleware=(), instructions="", workspace=None,
                   approved_commands=(), model_call_limit=24):
    """Reuse the Planner in standalone mode or as a checkpointed worker subgraph."""
    if workspace is not None:
        workspace = Path(workspace).resolve(strict=True)
        tools, policy = worker_policy(
            tools, "planner", workspace, approved_commands=approved_commands,
            model_call_limit=model_call_limit, include_questions=False,
        )
        middleware = [*policy, *middleware]
        response_schema = response_schema or DevelopmentPlan | ResearchRequest | Blocked
        instructions += runtime_instructions(workspace) + (
            "\nReturn ResearchRequest if additional investigation is "
            "needed; the Researcher will return evidence. Delegate substantial external "
            "research instead of duplicating the Researcher. If the user explicitly "
            "requests Researcher collaboration during planning, return ResearchRequest "
            "before finalizing the plan, even if you could research it yourself. "
            "Otherwise return DevelopmentPlan "
            "with concrete executable verification commands and the skill's Markdown plan. "
            "The workflow saves its plan outside the project workspace. Inspect "
            "existing workspace contents before choosing a scaffolding destination; "
            "use the root only if empty, or a new empty project subdirectory. "
            "If impossible, return Blocked. Do not produce a speculative plan."
        )
    response_schema = response_schema or Plan
    skill = SkillManager().load_skill("planner")
    return create_agent(
        model, tools=[*tools, ask_user], response_format=ToolStrategy(response_schema),
        middleware=[HumanInTheLoopMiddleware(
            interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}},
            interrupt_mode="per_call",
        ), *middleware],
        checkpointer=checkpointer,
        system_prompt="Investigate the supplied workspace using read-only operations. "
        "Ask essential questions via ask_user whenever missing information blocks progress, "
        "including after earlier answers. Report unavailable evidence and avoid secrets. "
        "Follow the planning skill. Never implement changes.\n\n" + skill["instructions"]
        + "\n\nApplication contract: return the complete final artifact as the structured "
        "response; the application saves it. Do not save files yourself. If Context7 is "
        "unavailable, use the available official documentation retrieval tools or report "
        "the evidence gap.\n" + instructions,
    )


def publish(workspace, plan):
    validate_plan(plan)
    filename, markdown = plan["filename"], plan["markdown"]
    data = markdown.encode("utf-8")
    path = workspace / filename
    with NamedTemporaryFile(dir=workspace, delete=False) as temp:
        staging = Path(temp.name)
    try:
        staging.write_bytes(data)
        try:
            os.link(staging, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                path = path.with_name(f"{path.stem.removesuffix('-plan')}-{hashlib.sha256(data).hexdigest()[:16]}-plan.md")
                try:
                    os.link(staging, path)
                except FileExistsError:
                    pass
        if path.is_symlink() or path.read_bytes() != data:
            raise RuntimeError(f"Plan publication conflict: {path}")
        return path
    finally:
        staging.unlink(missing_ok=True)


async def main(task=None):
    load_dotenv(Path(__file__).with_name(".env"))
    workspace = WORK_DIR.resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    thread = os.environ.get("PLANNER_THREAD") or str(uuid4())
    config = {"configurable": {"thread_id": thread}}
    print(f"Workspace: {workspace}\nResume with PLANNER_THREAD={thread}", flush=True)
    # Separate workspace histories and keep checkpoints outside the investigated tree.
    scope = hashlib.sha256(os.fsencode(str(workspace))).hexdigest()[:16]
    db = Path.home() / ".guide-planner" / f"{scope}.sqlite3"
    db.parent.mkdir(exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(db)) as checkpoints:
        checkpoint = await checkpoints.aget_tuple(config)
        if not checkpoint and os.environ.get("PLANNER_THREAD"):
            raise ValueError("Unknown planner thread for this workspace.")
        if checkpoint and (plan := checkpoint.checkpoint["channel_values"].get("structured_response")):
            print(f"Verified plan: {publish(workspace, plan)}")
            return
        base_url = os.environ.get("GOMODEL_BASE_URL", "http://localhost:8080/v1")
        key = os.environ.get("GOMODEL_MASTER_KEY")
        model = create_model(thread)
        async with gateway_tools(base_url, key) as tools:
            allowed = select_tools(tools, "planner")
            agent = create_planner(model, allowed, checkpointer=checkpoints,
                                   middleware=[WorkspaceGuard(workspace, allowed)])
            interrupts = (await agent.aget_state(config)).interrupts if checkpoint else ()
            request = None
            if not checkpoint:
                task = task or os.environ.get("PLANNER_TASK") or input("Planning request: ")
                if not task.strip():
                    raise ValueError("A planning request is required.")
                request = {"messages": [("user", f"Workspace: {workspace}\n\n{task}")]}
            while True:
                if interrupts:
                    answers = {}
                    for item in interrupts:
                        if not isinstance(item.value, dict) or item.value.get("name") != "ask_user":
                            raise ValueError(
                                "This checkpoint uses an incompatible human-input protocol. "
                                "Finish it with the original planner version, or unset "
                                "PLANNER_THREAD to start a new task."
                            )
                        print(item.value["args"]["questions"], flush=True)
                        answer = await asyncio.to_thread(input, "Answer (/pause to exit): ")
                        if answer.strip() in {"/pause", "/cancel"}:
                            return
                        if not answer.strip():
                            raise ValueError("An answer is required. Resume the thread to continue.")
                        answers[item.id] = {"type": "respond", "message": answer}
                    request = Command(resume=answers)
                result = await agent.ainvoke(request, config, durability="sync")
                interrupts = result.get("__interrupt__", ())
                if not interrupts:
                    if not (plan := result.get("structured_response")):
                        raise RuntimeError("Agent ended without a final plan.")
                    print(f"Verified plan: {publish(workspace, plan)}")
                    return


if __name__ == "__main__":
    asyncio.run(main())
