import asyncio
import json

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.tools import StructuredTool

from ntg_llm.react_parser import parse_react_output, render_mcp_tools


def test_mcp_json_schema_tool_receives_dictionary(tmp_path):
    destination = tmp_path / "example.py"

    async def write_file(path, content):
        from pathlib import Path

        Path(path).write_text(content)
        return "written"

    tool = StructuredTool(
        name="write_file",
        description="Write a file",
        args_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        coroutine=write_file,
    )
    arguments = {
        "path": str(destination),
        "content": "def factorial(n):\n    return 1\n",
    }
    action = parse_react_output(
        "Action: write_file\nAction Input: " + json.dumps(arguments)
    )
    assert asyncio.run(tool.arun(action.tool_input)) == "written"
    assert destination.read_text() == arguments["content"]
    assert '"required": ["path", "content"]' in render_mcp_tools([tool])


@pytest.mark.parametrize(
    "arguments", [".", '"some path"', "[]", "{}\nObservation: invented"]
)
def test_invalid_arguments_are_recoverable(arguments):
    with pytest.raises(OutputParserException) as error:
        parse_react_output("Action: list_directory\nAction Input: " + arguments)
    assert error.value.send_to_llm


def test_final_answer():
    assert parse_react_output("Final Answer: Done").return_values == {"output": "Done"}


def test_executor_recovers_and_calls_mcp_tool(tmp_path):
    from langchain_classic.agents import AgentExecutor, create_react_agent
    from langchain_core.language_models.fake import FakeListLLM
    from langchain_core.prompts import PromptTemplate
    from langchain_core.runnables import RunnableLambda

    received = []

    async def list_directory(path):
        received.append(path)
        return "example.py"

    tool = StructuredTool(
        name="list_directory",
        description="List files",
        args_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        coroutine=list_directory,
    )
    llm = FakeListLLM(
        responses=[
            "Action: list_directory\nAction Input: .",
            "Action: list_directory\nAction Input: "
            + json.dumps({"path": str(tmp_path)}),
            "Final Answer: Found example.py",
        ]
    )
    agent = create_react_agent(
        llm,
        [tool],
        PromptTemplate.from_template(
            "{tools}\n{tool_names}\n{input}\n{agent_scratchpad}"
        ),
        output_parser=RunnableLambda(parse_react_output),
        tools_renderer=render_mcp_tools,
    )
    executor = AgentExecutor(agent=agent, tools=[tool], handle_parsing_errors=True)
    result = asyncio.run(executor.ainvoke({"input": "List files"}))
    assert result["output"] == "Found example.py"
    assert received == [str(tmp_path)]
