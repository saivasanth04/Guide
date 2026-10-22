"""The first configured Agent, verified through the actual client with fake providers."""

import json
from unittest.mock import Mock

import pytest

from ntg_llm import LLMClient, LLMError
from ntg_llm.coder import CodeOutput, create_coder_agent
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore
from tests.helpers import FakeRouter, deployments


@pytest.fixture
def service(tmp_path):
    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.kwargs = kwargs
            return super().completion(**kwargs)

    return CompletionService(deployments(), RotationStore(tmp_path / "state.db"), RecordingRouter)


def test_coder_returns_source_unchanged_without_executing_it(service, tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    service.router.content = json.dumps({"code": source, "explanation": "A sample program."})
    result = create_coder_agent(LLMClient(service=service)).run("Generate source", "Use Python")
    assert isinstance(result.output, CodeOutput)
    assert result.output.code == source
    assert result.output.explanation == "A sample program."
    assert result.agent_name == "coder"
    assert result.diagnostics["attempt_count"] == 1
    assert result.model == service.deployments[0].model
    assert json.loads(result.model_dump_json())["output"]["code"] == source
    assert not marker.exists()
    assert len(service.router.calls) == 1
    messages = service.router.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "Python" in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {
        "task": "Generate source",
        "context": "Use Python",
    }
    assert "mock-secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "{}",
        '{"code": 3, "explanation":"x"}',
        '{"code":"print(1)", "explanation":false}',
        '{"code":"print(1)", "explanation":"x", "extra":1}',
        '{"code":"  ", "explanation":"x"}',
        '{"code":"print(1)", "explanation":"  "}',
        '{"code":"```python\\nprint(1)\\n```", "explanation":"x"}',
        '{"code":"def broken(:", "explanation":"x"}',
    ],
)
def test_invalid_coder_output_is_sanitized_without_retry_or_fallback(service, content):
    service.router.content = content
    with pytest.raises(LLMError) as error:
        create_coder_agent(LLMClient(service=service)).run("private-task", "private-context")
    assert error.value.status_code == 502
    assert error.value.diagnostics["agent_name"] == "coder"
    assert len(service.router.calls) == 1
    assert error.value.diagnostics["attempts"][0]["status"] == 502
    serialized = json.dumps(error.value.as_dict())
    for private in ("private-task", "private-context", "mock-secret", "def broken"):
        assert private not in serialized


def test_router_owns_retries_and_fallback_inside_one_agent_call(tmp_path):
    class Unavailable(Exception):
        status_code = 503

    class RecoveringRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Unavailable("mock-secret") if len(self.calls) < 3 else None
            return super().completion(**kwargs)

    service = CompletionService(
        deployments(),
        RotationStore(tmp_path / "state.db"),
        RecoveringRouter,
        sleeper=lambda _: None,
    )
    service.router.content = '{"code":"print(1)", "explanation":"Print a number."}'
    service.chat_completion = Mock(wraps=service.chat_completion)
    result = create_coder_agent(LLMClient(service=service)).run("Print a number")
    assert service.chat_completion.call_count == 1
    assert service.router.calls == [service.deployments[0].id] * 3 + [service.deployments[1].id]
    assert [attempt["status"] for attempt in result.diagnostics["attempts"]] == [503, 503, 503, 200]
    assert len(result.diagnostics["failed_attempts"]) == 3
    assert "mock-secret" not in result.model_dump_json()


def test_coder_construction_does_not_load_credentials(monkeypatch):
    from ntg_llm import client

    def unexpected_load():
        pytest.fail("Constructing an agent must not initialize a service")

    monkeypatch.setattr(client, "_get_service", unexpected_load)
    agent = create_coder_agent(LLMClient())
    assert agent.name == "coder"
    assert agent.response_model is CodeOutput
