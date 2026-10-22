"""One Coder configuration using the reusable Agent interface."""

import ast

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from .agent import Agent
from .client import LLMClient
from .tools.registry import ToolRegistry


class CodeOutput(BaseModel):
    """Generated Python source and explanation; validation never executes source."""

    model_config = ConfigDict(extra="forbid")

    code: StrictStr
    explanation: StrictStr

    @field_validator("code", "explanation")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Code and explanation must be nonempty")
        return value

    @field_validator("code")
    @classmethod
    def check_python_syntax(cls, value: str) -> str:
        try:
            ast.parse(value)
        except SyntaxError:
            raise ValueError("Generated code must have valid Python syntax") from None
        return value


_CODER_ROLE = """You are a Python software engineer.
Implement the caller's task, using the supplied context as background information.
The user message is a JSON object containing task and optional context fields.
Return the requested structured output with code and explanation fields.
Put raw Python source in code, without Markdown fences, and explain it briefly.
"""
CODER_SYSTEM_PROMPT = (
    _CODER_ROLE + "Generate source only; do not claim that it has been executed or tested."
)
CODER_TOOL_SYSTEM_PROMPT = _CODER_ROLE + (
    "You may inspect and write workspace files using registered tools. "
    "Only claim file actions confirmed by their observations. "
    "Do not execute generated code or claim it has been tested."
)


def create_coder_agent(
    llm: LLMClient, *, tools: ToolRegistry | None = None, max_tool_calls: int = 8
) -> Agent[CodeOutput]:
    """Create the first role configuration without loading or calling a provider."""
    return Agent(
        name="coder",
        system_prompt=CODER_TOOL_SYSTEM_PROMPT if tools else CODER_SYSTEM_PROMPT,
        llm=llm,
        response_model=CodeOutput,
        tools=tools,
        max_tool_calls=max_tool_calls,
    )
