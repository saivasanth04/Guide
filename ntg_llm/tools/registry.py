"""Immutable registration, closed decision schemas, and explicit tool execution."""

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, ValidationError, create_model, model_validator
from jsonschema import ValidationError as SchemaValidationError

from ..errors import RoutingError
from ..schemas import response_schema, validate_response
from .models import ToolError, ToolFailure, ToolResult, ToolSpec


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def check_action(self):
        if self.action == "tool" and self.tool is not None and self.output is None:
            return self
        if self.action == "final" and self.tool is None and self.output is not None:
            return self
        raise ValueError("Select exactly one tool action or final output")


@dataclass(frozen=True, init=False)
class ToolRegistry:
    _specs: MappingProxyType

    def __init__(self, specs):
        registered = {}
        for spec in specs:
            if (
                not isinstance(spec, ToolSpec)
                or not isinstance(spec.name, str)
                or re.fullmatch(r"[a-z][a-z0-9_]*", spec.name) is None
                or spec.name in registered
                or not callable(spec.handler)
                or not isinstance(spec.description, str)
                or not spec.description.strip()
            ):
                raise RoutingError(400, "Invalid or duplicate tool registration")
            response_schema(spec.input_model)
            response_schema(spec.output_model)
            registered[spec.name] = spec
        object.__setattr__(self, "_specs", MappingProxyType(registered))

    def __len__(self):
        return len(self._specs)

    def catalog(self):
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": response_schema(s.input_model),
            }
            for s in self._specs.values()
        ]

    def build_decision_model(self, final_model):
        response_schema(final_model)
        if not self:
            raise RoutingError(400, "A tool decision requires a nonempty registry")
        calls = [
            create_model(
                f"ToolCall_{s.name}",
                __config__=ConfigDict(extra="forbid"),
                name=(Literal[s.name], ...),
                arguments=(s.input_model, ...),
            )
            for s in self._specs.values()
        ]
        tool_type = Union[tuple(calls)]
        decision = create_model(
            "AgentDecision",
            __base__=_Decision,
            action=(Literal["tool", "final"], ...),
            tool=(tool_type | None, ...),
            output=(final_model | None, ...),
        )
        response_schema(decision)
        return decision

    def execute(self, name, arguments, *, call_id):
        spec = self._specs.get(name) if isinstance(name, str) else None
        name = name if isinstance(name, str) else "unknown"
        try:
            if spec is None:
                raise ToolError("unknown_tool")
            try:
                value = (
                    arguments.model_dump(mode="json")
                    if isinstance(arguments, BaseModel)
                    else arguments
                )
                # Closed schema validation also enforces fields for permissive input models.

                parsed = validate_response(
                    json.dumps(value), spec.input_model, response_schema(spec.input_model)
                )
            except (ValueError, TypeError, ValidationError, SchemaValidationError):
                raise ToolError("invalid_arguments") from None
            data = spec.handler(parsed)
            if isinstance(data, BaseModel):
                data = data.model_dump(mode="json")
            output = validate_response(
                json.dumps(data), spec.output_model, response_schema(spec.output_model)
            )
            return ToolResult[spec.output_model](call_id=call_id, tool=name, ok=True, data=output)
        except ToolError as error:
            return ToolResult[BaseModel](
                call_id=call_id,
                tool=name,
                ok=False,
                error=ToolFailure(code=error.code, message=str(error)),
            )
        except Exception:
            raise RoutingError(
                500,
                "Tool execution failed",
                {
                    "tool": name,
                    "call_id": call_id,
                    "code": "tool_execution_failed",
                    "effects_may_have_occurred": True,
                },
            ) from None
