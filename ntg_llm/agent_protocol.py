"""Schema-bound decisions while retaining LangChain's tools and ReAct execution loop."""

import json
from typing import Literal, Union

from langchain_classic.agents import create_react_agent
from langchain_classic.agents.format_scratchpad import format_log_to_str
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from pydantic import ConfigDict, create_model

from .react_parser import parse_react_output, render_mcp_tools


def context_text(value):
    """Keep source/traceback newlines readable instead of nesting JSON escapes."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(item, dict) and item.get("type") == "text" for item in value):
            return "\n".join(item.get("text", "") for item in value)
        return "\n\n".join(context_text(item) for item in value)
    if isinstance(value, dict):
        if "command" in value and "exit_code" in value:
            metadata = {key: item for key, item in value.items() if key not in {"stdout", "stderr"}}
            return json.dumps(metadata, indent=2) + "\nstdout:\n" + value.get("stdout", "") + "\nstderr:\n" + value.get("stderr", "")
        return "\n\n".join(f"{key}:\n{context_text(item)}" for key, item in value.items())
    return json.dumps(value)


def observation_text(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, dict) and "command" in parsed and "exit_code" in parsed:
            return context_text(parsed)
        return value
    return context_text(value)


def _argument_type(schema, name, definitions):
    if "$ref" in schema:
        return _argument_type(definitions[schema["$ref"].split("/")[-1]], name, definitions)
    if "enum" in schema:
        return Literal[tuple(schema["enum"])]
    alternatives = schema.get("anyOf", schema.get("oneOf"))
    if alternatives:
        return Union[
            tuple(
                _argument_type(item, name + str(i), definitions)
                for i, item in enumerate(alternatives)
            )
        ]
    kind = schema.get("type")
    if isinstance(kind, list):
        return Union[
            tuple(_argument_type({**schema, "type": value}, name, definitions) for value in kind)
        ]
    if kind == "array":
        return list[_argument_type(schema["items"], name + "Item", definitions)]
    if kind == "object":
        fields = {}
        for key, value in schema.get("properties", {}).items():
            annotation = _argument_type(value, name + key.title(), definitions)
            required = key in schema.get("required", [])
            fields[key] = (
                annotation if required else Union[annotation, None],
                ... if required else None,
            )
        return create_model(name, __config__=ConfigDict(extra="forbid"), **fields)
    return {"string": str, "integer": int, "number": float, "boolean": bool, "null": type(None)}[
        kind
    ]


def decision_schema(tools, *, allow_finish, final_model=str):
    branches = []
    for tool in tools:
        arguments = tool.args_schema
        if isinstance(arguments, dict):
            arguments = _argument_type(
                arguments, tool.name.title() + "Arguments", arguments.get("$defs", {})
            )
        branches.append(
            create_model(
                tool.name.title() + "Decision",
                __config__=ConfigDict(extra="forbid"),
                action=(Literal[tool.name], ...),
                arguments=(arguments, ...),
            )
        )
    if allow_finish:
        payload = create_model(
            "FinalPayload", __config__=ConfigDict(extra="forbid"), output=(final_model, ...)
        )
        branches.append(
            create_model(
                "FinalDecision",
                __config__=ConfigDict(extra="forbid"),
                action=(Literal["final_answer"], ...),
                arguments=(payload, ...),
            )
        )
    return create_model(
        "AgentDecision",
        __config__=ConfigDict(extra="forbid"),
        decision=(Union[tuple(branches)], ...),
    )


def build_react_agent(llm, tools, prompt, *, finish_guard, final_model=str):
    """Make premature completion unavailable in the provider's decision schema.

    Text-only/injected LangChain models keep the existing protocol. The NTG
    adapter uses the same router but requests validated JSON, then converts it
    into canonical ReAct text. No provider-native tool API is required.
    """
    if not getattr(llm, "structured_actions", False):
        return create_react_agent(
            llm,
            tools,
            prompt,
            output_parser=RunnableLambda(parse_react_output),
            tools_renderer=render_mcp_tools,
            stop_sequence=True,
        )
    prompt = prompt.partial(
        tools=render_mcp_tools(tools), tool_names=", ".join(t.name for t in tools)
    )
    contracts = {True: decision_schema(tools, allow_finish=True, final_model=final_model)}
    contracts[False] = decision_schema(tools, allow_finish=False, final_model=final_model) if tools else contracts[True]

    async def decide(inputs):
        steps = inputs["intermediate_steps"]
        allowed = finish_guard(steps)
        guarded = llm.model_copy(update={"decision_model": contracts[allowed]})
        if not allowed:
            inputs = {
                **inputs,
                "input": inputs["input"]
                + (
                    "\nExecution stage: completion is unavailable until required sandbox evidence exists. "
                    "Use the available tools to complete implementation/verification."
                ),
            }
        return await guarded.ainvoke(prompt.invoke(inputs))

    return (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda inputs: format_log_to_str([
                (action, observation_text(observation)) for action, observation in inputs["intermediate_steps"]
            ])
        )
        | RunnableLambda(decide)
        | RunnableLambda(parse_react_output)
    )


def format_decision(response):
    response = response.decision
    if response.action == "final_answer":
        output = response.arguments.output
        return "Final Answer: " + (output if isinstance(output, str) else output.model_dump_json())
    arguments = json.dumps(response.arguments.model_dump(exclude_none=True))
    return f"Action: {response.action}\nAction Input: {arguments}"
