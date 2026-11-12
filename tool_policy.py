"""Apply workspace/role boundaries to the existing, discovered GoModel tools.

MCP annotations are hints, not a sandbox. Only the configured filesystem and
keyless web server are trusted for unattended access. Desktop Commander shell
always needs explicit review or an exact operator-approved command.
"""

import asyncio
import operator
import os
import re
import shlex
from pathlib import Path
from typing import Annotated

from langchain.agents import AgentState
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.tools import tool as agent_tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

PATH_ARGUMENTS = {"path", "paths", "file_path", "source", "destination", "outputPath"}
PROTECTED_PARTS = {".git", ".aws", ".ssh", ".gnupg", ".guide-workflow"}


class WorkerState(AgentState):
    tool_events: Annotated[list[dict], operator.add]


def content_text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))


def tool_properties(tool):
    schema = tool.args_schema
    if not isinstance(schema, dict):
        schema = schema.model_json_schema() if schema else {}
    return schema.get("properties", {})


def select_tools(tools, role):
    """Filter the discovered catalog; never create another registry/client.

    Server slugs are explicit trust boundaries from config.yaml, not task routing.
    Unknown server tools stay unavailable until their operator policy is reviewed.
    """
    selected = []
    for tool in tools:
        if tool.name.startswith("filesystem_"):
            if role == "coder" or (tool.metadata or {}).get("readOnlyHint") is True:
                selected.append(tool)
        elif tool.name.startswith("web_"):
            # config.yaml restricts this server to search/fetch/link expansion.
            selected.append(tool)
        elif role == "coder" and tool.name.startswith("terminal_"):
            # No configuration, telemetry, unrestricted file access or global
            # process enumeration. Every exposed process operation is reviewed.
            properties = tool_properties(tool)
            if "command" in properties or "pid" in properties:
                selected.append(tool)
    return selected


def unread_processes(events):
    """Track process starts that have not yet had output read by this worker."""
    pending = set()
    for event in events:
        if event.get("status") != "success":
            continue
        if "process_id" in event:
            pending.add(event["process_id"])
        if event.get("tool") == "terminal_read_process_output":
            pending.discard(event.get("arguments", {}).get("pid"))
    return pending


def needs_execution_review(request, approved_commands=()):
    if "command" in request.tool_call["args"]:
        if unread_processes(request.state.get("tool_events", [])):
            # The guard returns corrective feedback without executing this call.
            return False
        return request.tool_call["args"]["command"] not in approved_commands
    pid = request.tool_call["args"].get("pid")
    if (request.tool.metadata or {}).get("readOnlyHint") is True and pid is not None:
        events = request.state.get("tool_events", [])
        owned = any(event.get("process_id") == pid for event in events)
        completed = any(event.get("arguments", {}).get("pid") == pid and "exit_code" in event
                        for event in events)
        return not owned or completed
    return True


class WorkspaceGuard(AgentMiddleware):
    state_schema = WorkerState

    def __init__(self, workspace, tools, *, max_output_chars=16000):
        self.workspace = Path(workspace).resolve(strict=True)
        self.names = {t.name for t in tools}
        self.max_output_chars = max_output_chars
        self._lock = asyncio.Lock()

    def checked_path(self, value):
        path = Path(value)
        path = (path if path.is_absolute() else self.workspace / path).resolve()
        if not path.is_relative_to(self.workspace):
            raise PermissionError(f"Path is outside the workspace: {value}")
        if any(part.lower() in PROTECTED_PARTS or part.lower().startswith(".env")
               for part in path.relative_to(self.workspace).parts):
            raise PermissionError(f"Protected path: {value}")
        return str(path)

    def checked_arguments(self, args):
        # Follow nested argument objects without confusing replacement text or
        # file contents with paths. Server-side allowed roots remain necessary.
        result = {}
        for key, value in args.items():
            if key in PATH_ARGUMENTS:
                result[key] = ([self.checked_path(v) for v in value]
                               if isinstance(value, list) else self.checked_path(value))
            elif isinstance(value, dict):
                result[key] = self.checked_arguments(value)
            else:
                result[key] = value
        return result

    def workspace_command(self, command, shell):
        """Set the initial directory in the same remote shell as the command."""
        name = Path(shell).stem.lower()
        if name in {"powershell", "pwsh"}:
            path = str(self.workspace).replace("'", "''")
            return f"Set-Location -LiteralPath '{path}' -ErrorAction Stop; {command}"
        if name == "cmd":
            if "%" in str(self.workspace):
                raise ValueError("Use PowerShell for workspace paths containing percent signs.")
            return f'cd /d "{self.workspace}" && {command}'
        if name in {"sh", "bash", "zsh", "dash"}:
            return f"cd -- {shlex.quote(str(self.workspace))} && {command}"
        raise ValueError(f"Unsupported terminal shell: {shell}. Use PowerShell, cmd, sh, bash or zsh.")

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        # Structured output and ask_user belong to LangChain, not the MCP policy.
        if request.tool is None or call["name"] == "ask_user":
            return await handler(request)
        if call["name"] not in self.names:
            raise PermissionError(f"Tool not authorized for this role: {call['name']}")
        try:
            args = self.checked_arguments(call["args"])
            if call["name"].startswith("terminal_") and "command" in args:
                pending = unread_processes(request.state.get("tool_events", []))
                if pending:
                    raise ValueError(
                        f"Read terminal_read_process_output for PID(s) {sorted(pending)} "
                        "before starting another command. Inspect errors or cancellation "
                        "and determine the cause before retrying."
                    )
        except (PermissionError, ValueError) as exc:
            args = call["args"]
            result = ToolMessage(
                content=f"{exc}. Selected workspace: {self.workspace}. "
                + ("Use paths within this workspace; relative paths resolve here."
                   if isinstance(exc, PermissionError) else ""),
                tool_call_id=call["id"], name=call["name"], status="error",
            )
        else:
            if call["name"].startswith("terminal_") and "command" in args:
                args["shell"] = args.get("shell") or ("powershell" if os.name == "nt" else "/bin/sh")
                args["command"] = self.workspace_command(args["command"], args["shell"])
            async with self._lock:
                result = await handler(request.override(tool_call={**call, "args": args}))
        if not isinstance(result, ToolMessage):
            raise TypeError(f"Unexpected MCP result from {call['name']}: {type(result).__name__}")
        text = content_text(result.content)
        event = {
            "id": call["id"], "tool": call["name"], "status": result.status,
            "arguments": {k: v for k, v in args.items()
                          if k in PATH_ARGUMENTS or k in {"command", "url", "query", "pid"}},
            "mutating": (request.tool.metadata or {}).get("readOnlyHint") is not True,
        }
        if "command" in args:
            # Verification and approvals refer to the original planned command;
            # retain the actual shell invocation separately for auditability.
            event["command"] = call["args"]["command"]
            event["arguments"]["command"] = event["command"]
            event["executed_command"] = args["command"]
            event["workspace"] = str(self.workspace)
        if "pid" in args:
            starts = [e for e in request.state.get("tool_events", [])
                      if e.get("process_id") == args["pid"]]
            if starts:
                event["command"] = starts[-1]["arguments"].get("command")
        # This is a documented Desktop Commander result format, not a model's
        # assertion. A started/running process is never counted as a passed test.
        process_match = re.search(r"Process started with PID (\d+)", text)
        if process_match and call["name"].startswith("terminal_"):
            event["process_id"] = int(process_match[1])
        # Only the trusted process-output reader's final footer establishes an
        # exit code. Text printed by the program in start_process is not evidence.
        exit_match = re.search(
            r"(?:^|\n)(?:[\u2705\u274c]\s*)?Process completed with exit code (-?\d+) "
            r"\(runtime: [^\n]+\)\s*$", text,
        )
        if (exit_match and "pid" in args
                and (request.tool.metadata or {}).get("readOnlyHint") is True):
            if event.get("command"):
                event["exit_code"] = int(exit_match[1])
        if len(text) > self.max_output_chars:
            text = text[:self.max_output_chars] + "\n[Output truncated; read a smaller range.]"
            result = result.model_copy(update={"content": text})
        return Command(update={"messages": [result], "tool_events": [event]})


@agent_tool
def ask_user(questions: str) -> str:
    """Ask essential questions together; ask again if the answer leaves a blocker."""
    # HumanInTheLoopMiddleware supplies the answer without executing this tool.
    raise RuntimeError("ask_user requires a human response through HITL middleware.")


def worker_policy(tools, role, workspace, *, approved_commands=(),
                  model_call_limit=24, include_questions=True):
    """Select discovered tools and apply the shared bounded execution policy."""
    allowed = select_tools(tools, role)
    if not any(t.name.startswith("filesystem_") for t in allowed):
        raise ValueError(f"GoModel exposes no authorized filesystem tools for {role}.")
    approvals = {"ask_user": {"allowed_decisions": ["respond"]}} if include_questions else {}
    for tool in allowed:
        if tool.name.startswith("terminal_"):
            approvals[tool.name] = {
                "allowed_decisions": ["approve", "reject"],
                "when": lambda request: needs_execution_review(request, approved_commands),
                "description": f"Review terminal operation. Initial working directory: {Path(workspace).resolve()}",
            }
    middleware = [
        WorkspaceGuard(workspace, allowed),
        ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=80, exit_behavior="error"),
    ]
    if approvals:
        middleware.insert(0, HumanInTheLoopMiddleware(
            interrupt_on=approvals, interrupt_mode="per_call",
        ))
    return allowed, middleware
