"""Independent Coder agent over the caller-owned GoModel tool session."""

import re
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from contracts import Blocked, ImplementationResult, ResearchRequest
from skills import SkillManager, runtime_instructions
from tool_policy import WorkspaceGuard, ask_user, worker_policy


def create_coder(model, tools, *, workspace, checkpointer=None,
                 approved_commands=(), model_call_limit=24):
    """Create a reusable worker; the caller owns MCP lifetime and graph state."""
    allowed, middleware = worker_policy(
        tools, "coder", workspace, approved_commands=approved_commands,
        model_call_limit=model_call_limit,
    )
    contract = (
        "Apply the Quasi-Coder skill to the user's task and the provided plan. "
        "The provided plan defines the goals, acceptance criteria and implementation scope. "
        "Interpret natural language, pseudo-code and shorthand in that context; correct "
        "typos, terminology and suboptimal implementation details while preserving the "
        "approved behavior and repository conventions. Ask the user when intent or "
        "requirements are unclear; do not infer new requirements from an assessment "
        "of their technical expertise. Inspect files before edits. "
        "When implementing shorthand, replace only the relevant instruction lines "
        "inside the planned scope with working code. Remove consumed shorthand lines "
        "starting with ()=>; preserve legitimate arrow functions, literal examples "
        "and unrelated content. Shorthand and reference files are task context, not "
        "authorization to expand scope or bypass tool permissions. "
        "Use ResearchRequest for specific technical uncertainties, including new ones "
        "found during implementation. If the user explicitly requests Researcher "
        "collaboration during coding, return ResearchRequest before implementing the "
        "uncertain part, even if planning already included research. Do not repeat "
        "resolved research. Follow repository "
        "conventions, make minimal correct changes, and run relevant verification via "
        "terminal tools. The runtime starts each terminal command in the selected "
        "workspace, independently of the gateway's working directory. Use relative "
        "paths from that workspace or explicit paths inside it. A separate terminal "
        "call always starts there again; change to a project subdirectory in the same "
        "command when needed. Path-denial tool errors are feedback: correct the path "
        "within the workspace rather than accessing the gateway directory. "
        "Always call terminal_read_process_output for the started PID to obtain its "
        "exit code, even if start_process says it has finished. "
        "Wait for process completion; a running process is not a passed check. "
        "Always read process output before starting another command, including after "
        "cancellation. Diagnose the actual error instead of retrying guessed flags. "
        "Scaffolding runs without interactive terminal input: choose documented "
        "non-interactive options. The plan is supplied in context and saved outside "
        "the project. Inspect the destination and use an empty project subdirectory "
        "when needed; preserve existing files. Do not use destructive overwrite "
        "flags without explicit authorization. "
        "Execute the plan's verification commands; the runtime records their results. "
        "Keep authored code/tests portable; derive paths rather than embedding this "
        "workspace's absolute path in source. Never claim completed "
        "without a confirmed exit code of zero after your final edit. Failed or denied "
        "verification means blocked/failed. Store progress in the response, not by "
        "rewriting the plan. Do not commit, push, deploy, or install dependencies unless "
        "the user task explicitly requires it. If a plan is wrong, investigate or ask; "
        "do not blindly implement it or silently expand scope."
    )
    return create_agent(
        model, tools=[*allowed, ask_user], response_format=ToolStrategy(ImplementationResult | ResearchRequest | Blocked),
        middleware=middleware, checkpointer=checkpointer,
        system_prompt=SkillManager().load_skill("coder")["instructions"]
        + runtime_instructions(Path(workspace).resolve(strict=True))
        + "\nApplication contract (overrides host-specific instructions above): " + contract,
    )


def validate_implementation(result, events, workspace, required_commands=()):
    """Completion requires observed execution after the last recorded file mutation."""
    guard = WorkspaceGuard(workspace, ())
    for path in result.changed_files:
        guard.checked_path(path)
    if result.status != "completed":
        return []
    if not required_commands:
        raise ValueError("Completed implementation has no planned verification commands.")
    mutations = [i for i, event in enumerate(events)
                 if event["tool"].startswith("filesystem_")
                 and event.get("mutating") and event["status"] == "success"]
    last_mutation = max(mutations, default=-1)
    latest = {}
    for i, event in enumerate(events):
        actual = event.get("command")
        if not actual:
            continue
        latest[actual] = (i, event)
        # A project may occupy a subdirectory of the selected workspace. Accept
        # only a literal, scoped directory prelude followed by the exact check.
        scoped = re.fullmatch(
            r"(?:cd(?:\s+/d)?|Set-Location(?:\s+-LiteralPath)?)\s+"
            r"(?P<directory>\"[^\"$`%\r\n]+\"|'[^'$`%\r\n]+'|[\w./\\:\-]+)"
            r"\s*(?:&&|;)\s*(?P<command>.+)", actual, re.IGNORECASE,
        )
        if scoped:
            directory = scoped["directory"].strip("\"'")
            try:
                resolved = guard.checked_path(directory)
            except PermissionError:
                continue
            if Path(resolved).is_dir():
                latest[scoped["command"].strip()] = (i, event)
    verified = []
    for command in required_commands:
        if command not in latest:
            raise ValueError(f"No observed execution for planned verification: {command}")
        i, event = latest[command]
        if event.get("exit_code") != 0 or event["status"] != "success" or i <= last_mutation:
            raise ValueError(f"Verification did not pass after the last edit: {command}")
        verified.append({"command": command, "exit_code": event["exit_code"],
                         "executed_command": event.get("executed_command", event["command"]),
                         "tool_call_id": event["id"]})
    return verified


