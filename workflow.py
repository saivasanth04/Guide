"""Checkpointed Planner/Researcher/Coder collaboration over GoModel-managed MCP."""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from coder import create_coder, validate_implementation
from contracts import (
    Blocked,
    DevelopmentPlan,
    ImplementationResult,
    ResearchReport,
    ResearchRequest,
    Source,
    WorkflowState,
)
from mcp_server import gateway_tools
from planner import WORK_DIR, create_model, create_planner, publish
from researcher import create_researcher, validate_report


@contextmanager
def workspace_lease(path):
    """Prevent concurrent CLI runs from mutating the same workspace."""
    with Path(path).open("a+b") as lease:
        if lease.tell() == 0:
            lease.write(b"\0")
            lease.flush()
        lease.seek(0)
        if os.name == "nt":
            import msvcrt
            lock = lambda: msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(lease, fcntl.LOCK_UN)
        try:
            lock()
        except OSError as exc:
            raise RuntimeError("Another Guide run owns this workspace; wait or pause it first.") from exc
        try:
            yield
        finally:
            unlock()


def build_workflow(model, tools, workspace, *, checkpointer=None, max_research=6,
                   max_handoffs=16, approved_commands=(), model_call_limit=24):
    """Use model-selected structured outcomes, not task keywords or a supervisor LLM."""
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    if checkpointer is not None and type(checkpointer.serde) is JsonPlusSerializer:
        # Worker structured responses appear in nested checkpoints too. Allow
        # only known schemas and their former workflow module names so existing
        # checkpoints remain readable after extraction into contracts.py.
        checkpointer.serde = JsonPlusSerializer(allowed_msgpack_modules=[
            (module, schema.__name__) for schema in
            (ResearchRequest, Source, ResearchReport, DevelopmentPlan, ImplementationResult, Blocked)
            for module in (schema.__module__, "workflow")
        ])
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("GoModel returned duplicate tool names.")
    workers = {
        role: factory(model, tools, workspace=workspace, approved_commands=approved_commands,
                      model_call_limit=model_call_limit)
        for role, factory in (("planner", create_planner), ("researcher", create_researcher),
                              ("coder", create_coder))
    }

    def budget(state):
        if state.get("handoffs", 0) >= max_handoffs:
            return {"status": "blocked", "result": {"reason": "Agent handoff budget exhausted."}}
        return None

    def context(state):
        if Path(state["workspace"]).resolve() != workspace:
            raise ValueError("Checkpoint workspace does not match the requested workspace.")
        return {
            "task": state["task"], "workspace": str(workspace), "plan": state.get("plan"),
            "research": list(state.get("research", {}).values())[-6:],
            "validation_feedback": state.get("feedback", ""),
        }

    async def invoke_worker(role, messages, events, request_context, config):
        history = trim_messages(
            messages, max_tokens=12000,
            token_counter=count_tokens_approximately, strategy="last", start_on="human",
            allow_partial=False,
        )
        return await workers[role].ainvoke(
            {"messages": [*history, HumanMessage(content=json.dumps(request_context))],
             "tool_events": events}, config,
        )

    async def act(role, state, config):
        if stopped := budget(state):
            return stopped
        result = await invoke_worker(
            role, state.get(f"{role}_messages", []), state.get("tool_events", []),
            context(state), config,
        )
        output = result.get("structured_response")
        update = {f"{role}_messages": result["messages"], "phase": role,
                  "handoffs": state.get("handoffs", 0) + 1, "status": "running"}
        # Seed the worker's native state with prior execution evidence so process
        # ownership survives a research handoff. Its reducer appends only new
        # events; do not concatenate the parent prefix a second time.
        events = result.get("tool_events", state.get("tool_events", []))
        update["tool_events"] = events
        if isinstance(output, ResearchRequest):
            update["request"] = output.model_dump()
            update.update(research_messages=[], research_events=[], research_feedback="", feedback="")
        elif isinstance(output, DevelopmentPlan):
            # Validate and publish before any Coder execution. This operation is
            # repeat-safe across checkpoint replay, using the existing publisher.
            # Keep orchestration artifacts out of scaffolders' project directory.
            scope = hashlib.sha256(os.fsencode(str(workspace))).hexdigest()[:16]
            plan_directory = Path(__file__).parent / ".guide-workflow" / "plans" / scope
            plan_directory.mkdir(parents=True, exist_ok=True)
            update.update(plan=output.model_dump(), request=None,
                          plan_path=str(publish(plan_directory, output.model_dump())), feedback="")
        elif isinstance(output, ImplementationResult):
            try:
                update["verification"] = validate_implementation(
                    output, events, workspace, state["plan"]["verification_commands"],
                )
            except ValueError as exc:
                # Deterministic acceptance failure is feedback, not a retry of an
                # ambiguous side effect. The bounded graph lets Coder obtain the
                # missing proof, correct its report, or return an honest blocker.
                update.update(feedback=str(exc), request=None)
                return update
            update.update(result=output.model_dump(), status=output.status, request=None, feedback="")
        elif isinstance(output, Blocked):
            update.update(result=output.model_dump(), status="blocked", request=None)
        else:
            raise RuntimeError(f"{role} ended without a valid structured outcome.")
        return update

    async def research(state, config):
        if stopped := budget(state):
            return stopped
        if state.get("research_calls", 0) >= max_research:
            return {"status": "blocked", "result": {"reason": "Research budget exhausted."}}
        # Include caller phase, request context and current repository metadata.
        # Changed files invalidate reuse; equivalent exact requests reuse evidence.
        revision = []
        for root, directories, filenames in os.walk(workspace):
            directories[:] = sorted(d for d in directories
                                    if not d.startswith(".") and d not in {"node_modules", "__pycache__"})
            for filename in sorted(filenames):
                path = Path(root) / filename
                if not filename.startswith(".") and not path.is_symlink():
                    stat = path.stat()
                    revision.append((str(path.relative_to(workspace)), stat.st_mtime_ns, stat.st_size))
        key = hashlib.sha256(json.dumps(
            [state["phase"], state["request"], sorted(revision)], sort_keys=True,
        ).encode()).hexdigest()
        reports = dict(state.get("research", {}))
        if key not in reports:
            request_context = {**context(state), "phase": state["phase"], "request": state["request"],
                               "validation_feedback": state.get("research_feedback", "")}
            result = await invoke_worker(
                "researcher", state.get("research_messages", []),
                state.get("research_events", []), request_context, config,
            )
            report = result.get("structured_response")
            if not isinstance(report, ResearchReport):
                raise RuntimeError("Researcher ended without a ResearchReport.")
            evidence = result.get("tool_events", [])
            try:
                validate_report(report, evidence)
            except ValueError as exc:
                return {"research_messages": result["messages"], "research_events": evidence,
                        "research_feedback": f"{exc}. Fetch/read the missing sources, or remove "
                        "unsupported claims and disclose the evidence gap. Return a corrected report.",
                        "research_calls": state.get("research_calls", 0) + 1,
                        "handoffs": state.get("handoffs", 0) + 1}
            reports[key] = {"phase": state["phase"], "request": state["request"], **report.model_dump()}
        return {"research": reports, "request": None,
                "research_feedback": "", "research_messages": [], "research_events": [],
                "research_calls": state.get("research_calls", 0) + 1,
                "handoffs": state.get("handoffs", 0) + 1}

    def route(state):
        if state.get("status") in {"completed", "blocked", "failed"}:
            return END
        if state.get("request"):
            return "researcher"
        return "coder"

    builder = StateGraph(WorkflowState)
    builder.add_node("planner", partial(act, "planner"))
    builder.add_node("researcher", research)
    builder.add_node("coder", partial(act, "coder"))
    builder.add_edge(START, "planner")
    builder.add_conditional_edges("planner", route, ["researcher", "coder", END])
    builder.add_conditional_edges("coder", route, ["researcher", "coder", END])
    builder.add_conditional_edges(
        "researcher",
        lambda s: END if s.get("status") == "blocked" else
        "researcher" if s.get("research_feedback") else s["phase"],
        ["planner", "coder", "researcher", END],
    )
    return builder.compile(checkpointer=checkpointer)


async def run_cli(args):
    load_dotenv(Path(__file__).with_name(".env"))
    workspace = args.workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    thread = args.thread or str(uuid4())
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 100}
    print(f"Workspace: {workspace}\nThread: {thread}", flush=True)
    db_dir = Path(__file__).parent / ".guide-workflow"
    db_dir.mkdir(exist_ok=True)
    scope = hashlib.sha256(os.fsencode(str(workspace))).hexdigest()[:16]
    with workspace_lease(db_dir / f"{scope}.lock"):
        try:
            return await run_session(args, workspace, thread, config, db_dir / f"{scope}.sqlite3")
        except Exception as exc:
            if any(detail.startswith("APITimeoutError:") for detail in failure_details(exc)):
                print(
                    "Model request timed out after bounded retries. Checkpoint preserved.\n"
                    f'Resume: "{sys.executable}" "{Path(__file__).resolve()}" '
                    f'--workspace "{workspace}" --thread {thread}',
                    file=sys.stderr, flush=True,
                )
            raise


async def run_session(args, workspace, thread, config, db):
    async with AsyncSqliteSaver.from_conn_string(str(db)) as saver:
        async with gateway_tools(os.getenv("GOMODEL_BASE_URL", "http://localhost:8080/v1"),
                                 os.getenv("GOMODEL_MASTER_KEY")) as tools:
            graph = build_workflow(create_model(thread), tools, workspace, checkpointer=saver,
                                   approved_commands=args.approve_command)
            snapshot = await graph.aget_state(config)
            if args.thread and not snapshot.values:
                raise ValueError("Unknown workflow thread for this workspace.")
            if snapshot.values and not snapshot.next:
                print(json.dumps(public_result(snapshot.values), indent=2))
                return snapshot.values.get("status") == "completed"
            request = None
            if not snapshot.values:
                task = args.task or input("Development task: ")
                if not task.strip():
                    raise ValueError("A development task is required.")
                request = {"task": task, "workspace": str(workspace), "status": "running"}
            while True:
                if snapshot.interrupts:
                    responses = {}
                    for item in snapshot.interrupts:
                        value = item.value
                        if value.get("name") == "ask_user":
                            print(value["args"]["questions"], flush=True)
                            answer = await asyncio.to_thread(input, "Answer (/pause to exit): ")
                            if answer.strip() == "/pause":
                                return False
                            if not answer.strip():
                                raise ValueError("An answer is required; resume this thread to continue.")
                            responses[item.id] = {"type": "respond", "message": answer}
                        else:
                            print(json.dumps(value, indent=2), flush=True)
                            answer = await asyncio.to_thread(input, "Execute? [approve/reject/pause]: ")
                            if answer.strip() == "pause":
                                return False
                            if answer.strip() not in {"approve", "reject"}:
                                raise ValueError("Expected approve, reject, or pause; checkpoint preserved.")
                            responses[item.id] = {"type": answer.strip()}
                    request = Command(resume=responses)
                result = await graph.ainvoke(request, config, durability="sync")
                if not result.get("__interrupt__"):
                    print(json.dumps(public_result(result), indent=2))
                    return result.get("status") == "completed"
                snapshot = await graph.aget_state(config)


def public_result(state):
    result = state.get("result", {})
    return {"status": state.get("status"), "plan_path": state.get("plan_path"), "result": result,
            "verification": state.get("verification", [])}


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?")
    parser.add_argument("--workspace", type=Path, default=WORK_DIR)
    parser.add_argument("--thread", help="Resume an existing workflow in this workspace")
    parser.add_argument("--approve-command", action="append", default=[],
                        help="Exact operator-reviewed shell command; all other shell calls require HITL")
    args = parser.parse_args()
    if args.thread and args.task:
        parser.error("Use --thread to resume existing work, or supply a task for a new thread.")
    try:
        return 0 if asyncio.run(run_cli(args)) else 2
    except Exception as exc:
        print("Workflow failed: " + "; ".join(failure_details(exc)), file=sys.stderr)
        return 1


def failure_details(error):
    """MCP task groups may wrap the real failure; show the leaves to the operator."""
    children = getattr(error, "exceptions", ())
    return ([detail for child in children for detail in failure_details(child)]
            if children else [f"{type(error).__name__}: {error}"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # Shared schemas live in contracts.py in both CLI and imported use.
    raise SystemExit(cli())
