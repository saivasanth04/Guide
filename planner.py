import asyncio
import hashlib
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from typing_extensions import Annotated, TypedDict

from mcp_server import gateway_tools
from skills import SkillManager


WORK_DIR = Path(r"C:\Users\perur\Desktop\workspace")


class Plan(TypedDict):
    """Final validated planning artifact, including verification steps and remaining risks."""

    filename: Annotated[str, "A kebab-case filename ending in -plan.md"]
    markdown: Annotated[str, "The complete Markdown plan following the supplied skill"]


@tool
def ask_user(questions: str) -> str:
    """Ask essential questions together; ask again if the answer leaves a blocker."""
    # HumanInTheLoopMiddleware supplies the answer without executing this tool.
    raise RuntimeError("ask_user requires a human response through HITL middleware.")


def publish(workspace, plan):
    filename, markdown = plan["filename"], plan["markdown"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-plan\.md", filename):
        raise ValueError(f"Invalid plan filename: {filename!r}")
    if not re.search(r"^# .+\n", markdown, re.M) or not re.search(r"^## .+", markdown, re.M):
        raise ValueError("Expected a Markdown plan with a title and sections.")
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
        model = init_chat_model(
            model=os.environ.get("GOMODEL_MODEL", "router"), model_provider="openai",
            base_url=base_url, api_key=key or "dummy-key", timeout=120, max_retries=0,
            default_headers={"X-Session-ID": thread, "x-session-affinity": thread},
            temperature=0.2,
        )
        skill = SkillManager().load_skill("planner")
        async with gateway_tools(base_url, key) as tools:
            agent = create_agent(
                model, tools=[*tools, ask_user], response_format=ToolStrategy(Plan),
                middleware=[HumanInTheLoopMiddleware(
                    interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}},
                    interrupt_mode="per_call",
                )],
                checkpointer=checkpoints,
                system_prompt="Investigate the supplied workspace using read-only operations. "
                "Ask essential questions via ask_user whenever missing information blocks progress, "
                "including after earlier answers. Report unavailable evidence and avoid secrets. "
                "Follow the planning skill. Validate your plan and return the complete final artifact "
                "as the structured response. The application saves it. Never implement changes.\n\n"
                + skill["instructions"],
            )
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
