"""Per-run host evidence, without prompts, scratchpads, or model reasoning."""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from .errors import RoutingError


@dataclass
class AgentTrace:
    steps: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    model: str = ""
    stopped: bool = False

    def record_model(self, diagnostics, model=None):
        self.steps.append(
            {"turn_number": len(self.steps) + 1, "diagnostics": deepcopy(diagnostics)}
        )
        if model is not None:
            self.model = model

    def record_tool(self, result):
        trace = {"call_id": result.call_id, "tool": result.tool, "ok": result.ok}
        if not result.ok:
            trace["error_code"] = result.error.code
        else:
            path = getattr(result.data, "path", None)
            if isinstance(path, str):
                parsed = PureWindowsPath(path)
                if (
                    not parsed.drive
                    and not parsed.root
                    and ".." not in parsed.parts
                    and not any(ord(c) < 32 for c in path)
                ):
                    trace["path"] = path
            for key in ("bytes_read", "bytes_written"):
                value = getattr(result.data, key, None)
                if type(value) is int:
                    trace[key] = value
            entries = getattr(result.data, "entries", None)
            if isinstance(entries, list):
                trace["entry_count"] = len(entries)
                trace["truncated"] = getattr(result.data, "truncated", False)
        self.tool_calls.append(trace)

    def diagnostics(self):
        result = deepcopy(self.steps[-1]["diagnostics"]) if self.steps else {}
        attempts = []
        count = 0
        for step in self.steps:
            details = step["diagnostics"]
            records = details.get("attempts", [])
            count += details.get("attempt_count", len(records))
            attempts.extend(
                {**deepcopy(item), "turn_number": step["turn_number"]} for item in records
            )
        result.update(
            attempts=attempts,
            failed_attempts=[item for item in attempts if item.get("status") != 200],
            attempt_count=count,
            agent_steps=deepcopy(self.steps),
            agent_step_count=len(self.steps),
            tool_calls=deepcopy(self.tool_calls),
            agent_runtime="langchain-classic.AgentExecutor",
        )
        return result

    def failure(self, error):
        aggregate = self.diagnostics()
        diagnostics = {**aggregate, **deepcopy(error.diagnostics)}
        # A provider's last-turn attempt list must not overwrite the run aggregate.
        for key in (
            "attempts",
            "failed_attempts",
            "attempt_count",
            "agent_steps",
            "agent_step_count",
            "tool_calls",
            "agent_runtime",
        ):
            diagnostics[key] = aggregate[key]
        return RoutingError(
            error.status_code,
            str(error),
            diagnostics,
            error.retry_after,
            transient=error.transient,
            failover=error.failover,
        )
