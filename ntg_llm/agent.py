"""A stateless role and output contract over the existing LLM client."""

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from .client import LLMClient
from .errors import RoutingError
from .schemas import Answer, response_schema
from .tools.registry import ToolRegistry

OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentResult(BaseModel, Generic[OutputT]):
    """Validated agent output with its model and sanitized routing diagnostics."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    output: OutputT
    model: str
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class Agent(Generic[OutputT]):
    """Configure a role once; each run supplies its own task and optional context.

    Construction validates configuration without loading credentials. Tool-free
    runs make one client invocation; tools use LangChain's ReAct factory/executor.
    max_tool_calls maps to the executor's single-action iteration limit.
    Upstream retries remain owned by the existing router.
    """

    name: str
    system_prompt: str = field(repr=False)
    llm: LLMClient = field(repr=False)
    response_model: type[OutputT] = field(default=Answer, kw_only=True)
    tools: ToolRegistry | None = field(default=None, kw_only=True, repr=False)
    max_tool_calls: int = field(default=8, kw_only=True)

    def __post_init__(self):
        if type(self.max_tool_calls) is not int or self.max_tool_calls < 1:
            raise RoutingError(400, "max_tool_calls must be a positive integer")
        if self.tools is not None and not isinstance(self.tools, ToolRegistry):
            raise RoutingError(400, "tools must be a ToolRegistry or None")
        for label, value in (("name", self.name), ("system_prompt", self.system_prompt)):
            if not isinstance(value, str) or not value.strip():
                raise RoutingError(400, f"Agent {label} must be a nonempty string")
        try:
            response_schema(self.response_model)
        except RoutingError as error:
            raise self._with_identity(error) from None

    def _with_identity(self, error: RoutingError) -> RoutingError:
        diagnostics = deepcopy(error.diagnostics)
        diagnostics["agent_name"] = self.name
        return RoutingError(
            error.status_code,
            str(error),
            diagnostics,
            error.retry_after,
            transient=error.transient,
            failover=error.failover,
        )

    def run(self, task: str, context: str | None = None) -> AgentResult[OutputT]:
        """Return typed output without retaining the task or context between runs."""
        if not isinstance(task, str) or not task.strip():
            raise RoutingError(400, "task must be a nonempty string", {"agent_name": self.name})
        if context is not None and not isinstance(context, str):
            raise RoutingError(400, "context must be text or None", {"agent_name": self.name})
        messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": json.dumps({"task": task, "context": context}, ensure_ascii=False),
            },
        ]
        try:
            if self.tools:
                from .tool_runtime import run_langchain_agent

                outcome = run_langchain_agent(
                    self.llm, messages, self.response_model, self.tools, self.max_tool_calls
                )
                return AgentResult[self.response_model](
                    agent_name=self.name,
                    output=outcome.output,
                    model=outcome.model,
                    diagnostics=outcome.diagnostics,
                )
            response = self.llm.chat.completions.create(
                model="router",
                messages=messages,
                response_model=self.response_model,
            )
        except RoutingError as error:
            raise self._with_identity(error) from None
        return AgentResult[self.response_model](
            agent_name=self.name,
            output=response.choices[0].message.parsed,
            model=response.model,
            diagnostics=deepcopy(response.diagnostics),
        )
