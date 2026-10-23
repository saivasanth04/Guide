"""Parse textual ReAct actions into the JSON objects required by MCP tools."""

import json
import re

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.exceptions import OutputParserException


def parse_react_output(text):
    action = re.search(
        r"^Action:\s*([^\r\n]+)\r?\nAction Input:\s*(.*)\Z",
        text,
        re.MULTILINE | re.DOTALL,
    )
    final = re.search(r"^Final Answer:\s*(.*)\Z", text, re.MULTILINE | re.DOTALL)
    if final and not action:
        return AgentFinish({"output": final.group(1).strip()}, text)
    if action and not final:
        try:
            arguments = json.loads(action.group(2).strip())
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
        except ValueError:
            raise OutputParserException(
                "Action Input must be a JSON object",
                observation='Use Action Input: {"path": "absolute path"} with the tool schema. '
                "Return one action and no Observation text or Markdown fences.",
                llm_output=text,
                send_to_llm=True,
            ) from None
        return AgentAction(action.group(1).strip(), arguments, text)
    raise OutputParserException(
        "Expected one Action/Action Input pair or Final Answer",
        observation="Return one Action with a JSON object as Action Input, or Final Answer.",
        llm_output=text,
        send_to_llm=True,
    )


def render_mcp_tools(tools):
    descriptions = []
    for tool in tools:
        schema = tool.args_schema
        if not isinstance(schema, dict):
            schema = schema.model_json_schema()
        descriptions.append(
            f"{tool.name}: {tool.description}\nInput JSON schema: {json.dumps(schema)}"
        )
    return "\n\n".join(descriptions)
