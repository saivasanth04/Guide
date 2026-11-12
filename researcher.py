"""Independent Researcher agent over the caller-owned GoModel tool session."""

from pathlib import Path
from urllib.parse import urldefrag

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from contracts import ResearchReport
from skills import SkillManager, runtime_instructions
from tool_policy import ask_user, worker_policy


def create_researcher(model, tools, *, workspace, checkpointer=None,
                     approved_commands=(), model_call_limit=24):
    """Create a reusable worker; the caller owns MCP lifetime and graph state."""
    allowed, middleware = worker_policy(
        tools, "researcher", workspace, approved_commands=approved_commands,
        model_call_limit=model_call_limit,
    )
    contract = (
        "Research the request for the specified planning/coding phase. Inspect relevant "
        "repository files and retrieve primary documentation. For every cited source, "
        "call its read/fetch tool during this request using the exact source URL/path. "
        "Do not cite search snippets as read documents. Return findings, alternatives, "
        "compatibility, actionable examples where relevant, recommendations and gaps. "
        "Do not implement or create another implementation plan. A single authoritative "
        "source is enough when it resolves the request. Use available MCP tools in "
        "place of upstream skill-specific tool names."
    )
    return create_agent(
        model, tools=[*allowed, ask_user], response_format=ToolStrategy(ResearchReport),
        middleware=middleware, checkpointer=checkpointer,
        system_prompt=SkillManager().load_skill("researcher")["instructions"]
        + runtime_instructions(Path(workspace).resolve(strict=True))
        + "\nApplication contract (overrides host-specific instructions above): " + contract,
    )


def validate_report(report, events):
    """Reject invented source locations; content/inference still needs model review."""
    def canonical(location):
        if location.startswith(("http://", "https://")):
            return urldefrag(location)[0]
        return str(Path(location).resolve()) if Path(location).is_absolute() else location

    accessed = set()
    for event in events:
        if event["status"] != "success":
            continue
        args = event["arguments"]
        for key in ("url", "path", "paths"):
            value = args.get(key, [])
            accessed.update(canonical(v) for v in (value if isinstance(value, list) else [value]))
    for source in report.sources:
        if canonical(source.location) not in accessed:
            raise ValueError(f"Research cited a source it did not read: {source.location}")
    if not report.sources and not report.limitations:
        raise ValueError("Research without sources must disclose its limitations.")


