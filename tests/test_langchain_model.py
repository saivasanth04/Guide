"""The LangChain adapter delegates to the existing NTG backend without recovery."""

import json
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ntg_llm import Agent, Answer, LLMClient, LLMError
from ntg_llm.agent_trace import AgentTrace
from ntg_llm.langchain_model import NTGChatModel
from ntg_llm.langchain_tools import create_langchain_tools
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore
from ntg_llm.tools import ToolRegistry, ToolSpec
from tests.helpers import FakeRouter, deployments


def backend(tmp_path):
    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.messages = kwargs["messages"]
            self.schema = kwargs["response_format"]["json_schema"]["schema"]
            return super().completion(**kwargs)

    service = CompletionService(
        deployments(), RotationStore(tmp_path / "state.db"), RecordingRouter
    )
    return service, LLMClient(service=service)


def test_adapter_converts_messages_uncached_and_preserves_router_selection(tmp_path):
    service, client = backend(tmp_path)
    trace = AgentTrace()
    model = NTGChatModel(client=client, trace=trace)
    messages = [
        SystemMessage(content="Role"),
        HumanMessage(content="Task"),
        AIMessage(content="Past"),
    ]
    for _ in range(2):
        result = model.invoke(messages)
        assert isinstance(result, AIMessage) and result.content == "actual answer"
    assert service.router.messages == [
        {"role": "system", "content": "Role"},
        {"role": "user", "content": "Task"},
        {"role": "assistant", "content": "Past"},
    ]
    assert list(service.router.schema["properties"]) == ["answer"]
    assert len(service.router.calls) == 2 and service.router.calls[0] != service.router.calls[1]
    assert len(trace.steps) == 2 and trace.model == service.deployments[1].model
    assert "client" not in model.model_dump() and "trace" not in model.model_dump()
    assert "mock-secret" not in repr(model)


@pytest.mark.parametrize(
    "messages,options",
    [
        ([ToolMessage(content="result", tool_call_id="one")], {}),
        ([HumanMessage(content=[{"type": "text", "text": "multimodal"}])], {}),
        ([HumanMessage(content="hi")], {"stop": ["stop"]}),
        ([HumanMessage(content="hi")], {"temperature": 0}),
    ],
)
def test_adapter_rejects_unsupported_features_before_router(tmp_path, messages, options):
    service, client = backend(tmp_path)
    with pytest.raises(LLMError) as error:
        NTGChatModel(client=client).invoke(messages, **options)
    assert error.value.status_code == 400 and service.router.calls == []


def test_langchain_tool_preserves_strict_host_validation_without_coercion():
    from pydantic import BaseModel

    class Number(BaseModel):
        value: int = 1

    calls = []

    def handler(args):
        calls.append(args)
        return Answer(answer=str(args.value))

    trace = AgentTrace()
    registry = ToolRegistry([ToolSpec("number", "Number", Number, Answer, handler)])
    tool = create_langchain_tools(registry, trace)[0]
    for args in ({}, {"value": "1"}, {"value": True}, {"value": 1, "extra": 2}):
        result = json.loads(tool.invoke(args))
        assert result["error"]["code"] == "invalid_arguments"
    assert calls == []
    assert json.loads(tool.invoke({"value": 3}))["data"]["answer"] == "3"
    assert len(calls) == 1 and len(trace.tool_calls) == 5


def test_direct_apis_and_tool_free_agent_do_not_import_executor(tmp_path, monkeypatch):
    service, client = backend(tmp_path)
    monkeypatch.setitem(sys.modules, "ntg_llm.tool_runtime", None)
    direct = client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert direct.choices[0].message.parsed.answer == "actual answer"
    assert service.structured_completion("hi")["answer"] == "actual answer"
    assert Agent("helper", "Help", client).run("hi").output.answer == "actual answer"
    assert len(service.router.calls) == 3
