"""Typed LangChain tools that retain NTG host validation and filesystem policy."""

from langchain_core.tools import StructuredTool

from .agent_trace import AgentTrace
from .errors import RoutingError
from .schemas import response_schema
from .tools.registry import ToolRegistry


def create_langchain_tools(registry: ToolRegistry, trace: AgentTrace):
    """Expose immutable registrations; the LangGraph tool node invokes each tool."""

    def adapt(spec):
        def observe(result):
            trace.record_tool(result)
            return result.model_dump_json()

        def execute(**arguments):
            call_id = f"tool_{len(trace.tool_calls) + 1:03d}"
            try:
                result = registry.execute(spec.name, arguments, call_id=call_id)
            except RoutingError:
                trace.tool_calls.append(
                    {
                        "call_id": call_id,
                        "tool": spec.name,
                        "ok": False,
                        "error_code": "tool_execution_failed",
                        "effects_may_have_occurred": True,
                    }
                )
                raise
            return observe(result)

        return StructuredTool.from_function(
            func=execute,
            name=spec.name,
            description=spec.description,
            # JSON schemas avoid Pydantic coercion/default insertion before NTG's
            # strict validation. The registry validates the original argument dict.
            args_schema=response_schema(spec.input_model),
        )

    return [adapt(spec) for spec in registry.specs]
