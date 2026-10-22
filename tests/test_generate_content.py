"""Facade integration against the real router service with an offline provider."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from ntg_llm import LLMClient, LLMError
from ntg_llm.schemas import Deployment
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore


class OfflineRouter:
    def __init__(self, **kwargs):
        self.model_list = kwargs["model_list"]
        self.calls = []
        self.text = "Plain generated text"
        self.errors = []

    def set_custom_routing_strategy(self, strategy):
        self.strategy = strategy

    def completion(self, **kwargs):
        selected = self.strategy.get_available_deployment(**kwargs)
        self.calls.append((selected["model_info"]["id"], kwargs))
        if self.errors:
            error = self.errors.pop(0)
            if error:
                raise error
        return NS(choices=[NS(message=NS(content=self.text))])


@pytest.fixture
def backend(tmp_path):
    service = CompletionService(
        [
            Deployment("one", None, "gemini-a", "mock-secret-1"),
            Deployment("two", None, "gemini-b", "mock-secret-2"),
            Deployment("three", None, "gemini-a", "mock-secret-3"),
        ],
        RotationStore(tmp_path / "state.db"),
        OfflineRouter,
        sleeper=lambda _: None,
    )
    return service, LLMClient(service=service)


def test_plain_text_and_automatic_round_robin(backend):
    service, client = backend
    results = [client.models.generate_content(model="router", contents="Hello") for _ in range(2)]
    assert [r.model_version for r in results] == ["gemini-a", "gemini-b"]
    assert all(r.text == "Plain generated text" and r.parsed is None for r in results)
    assert all("response_format" not in kwargs for _, kwargs in service.router.calls)
    assert service.router.calls[0][1]["messages"] == [{"role": "user", "content": "Hello"}]
    assert "mock-secret" not in results[0].model_dump_json()


def test_explicit_model_rotates_only_matching_accounts(backend):
    service, client = backend
    for _ in range(2):
        assert (
            client.models.generate_content(model="models/gemini-a", contents="Hi").model_version
            == "gemini-a"
        )
    assert [key for key, _ in service.router.calls] == [
        service.deployments[0].id,
        service.deployments[2].id,
    ]


def test_native_content_shapes_and_generation_config(backend):
    service, client = backend
    client.models.generate_content(
        model="router",
        contents=[
            {"role": "user", "parts": [{"text": "First"}]},
            {"role": "model", "parts": [{"text": "Reply"}]},
            "Next",
            {"text": "question"},
        ],
        config={
            "system_instruction": "Be brief",
            "temperature": 0.2,
            "max_output_tokens": 128,
            "top_k": 10.0,
            "top_p": 0.8,
            "seed": 3,
            "stop_sequences": ["END"],
        },
    )
    kwargs = service.router.calls[0][1]
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user", "assistant", "user"]
    assert kwargs["messages"][-1]["content"] == "Next\nquestion"
    assert kwargs["temperature"] == 0.2 and kwargs["max_tokens"] == 128
    assert kwargs["top_k"] == 10 and kwargs["stop"] == ["END"]


def test_sdk_objects_if_installed(backend):
    types = pytest.importorskip("google.genai.types")
    service, client = backend
    client.models.generate_content(
        model="router",
        contents=types.Content(role="user", parts=[types.Part(text="Hello")]),
        config=types.GenerateContentConfig(temperature=0, top_k=10, max_output_tokens=32),
    )
    kwargs = service.router.calls[0][1]
    assert kwargs["messages"] == [{"role": "user", "content": "Hello"}]
    assert kwargs["top_k"] == 10 and kwargs["max_tokens"] == 32


def test_structured_response(backend):
    class Result(BaseModel):
        value: int

    service, client = backend
    service.router.text = '{"value":7}'
    response = client.models.generate_content(
        model="router",
        contents="Return seven",
        config={"response_mime_type": "application/json", "response_schema": Result},
    )
    assert response.parsed.value == 7
    assert response.text == '{"value":7}'
    assert service.router.calls[0][1]["response_format"]["type"] == "json_schema"


def test_json_without_schema(backend):
    service, client = backend
    service.router.text = '{"value":7}'
    response = client.models.generate_content(
        model="router", contents="Return JSON", config={"response_mime_type": "application/json"}
    )
    assert response.parsed == {"value": 7}
    assert service.router.calls[0][1]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "arguments",
    [
        {"contents": ""},
        {"contents": []},
        {"contents": {"inline_data": {"data": "hidden"}}},
        {"config": {"tools": []}},
        {"config": {"max_output_tokens": True}},
        {"config": {"temperature": -1}},
        {"config": {"candidate_count": 2}},
        {"config": {"response_schema": {"type": "object"}}},
        {"contents": {"role": "system", "parts": [{"text": "hidden"}]}},
    ],
)
def test_unsupported_or_invalid_input_stops_before_backend(arguments):
    client = LLMClient(service=Mock())
    with pytest.raises(LLMError) as error:
        client.models.generate_content(**{"model": "router", "contents": "Hello", **arguments})
    assert error.value.status_code == 400
    client.models._service.generate_content.assert_not_called()


def test_unknown_model_does_not_call_provider(backend):
    service, client = backend
    with pytest.raises(LLMError) as error:
        client.models.generate_content(model="not-configured", contents="Hi")
    assert error.value.status_code == 400 and not service.router.calls


def test_existing_chat_interface_is_unchanged(backend):
    service, client = backend
    service.router.text = '{"answer":"Existing chat"}'
    response = client.chat.completions.create(messages=[{"role": "user", "content": "Hello"}])
    assert response.choices[0].message.content == "Existing chat"
    assert service.router.calls[0][1]["response_format"]["type"] == "json_schema"


def test_provider_retry_stays_in_the_service(backend):
    class Unavailable(Exception):
        status_code = 503

    service, client = backend
    service.router.errors = [Unavailable("private provider detail"), None]
    result = client.models.generate_content(model="router", contents="Hello")
    assert result.text == "Plain generated text"
    assert service.router.calls[0][0] == service.router.calls[1][0]
    assert result.diagnostics["attempt_count"] == 2
    assert result.diagnostics["failed_attempts"][0]["status"] == 503


def test_existing_answer_model_is_valid_as_response_schema(backend):
    from ntg_llm import Answer

    service, client = backend
    service.router.text = '{"answer":"Typed answer"}'
    result = client.models.generate_content(
        model="router",
        contents="Hello",
        config={"response_schema": Answer, "response_mime_type": "application/json"},
    )
    assert result.parsed.answer == "Typed answer"
    assert result.text == '{"answer":"Typed answer"}'


def test_native_config_with_typed_response_schema(backend):
    types = pytest.importorskip("google.genai.types")
    from datetime import date

    class Event(BaseModel):
        day: date

    service, client = backend
    service.router.text = '{"day":"2026-10-22"}'
    result = client.models.generate_content(
        model="router",
        contents="Return the date",
        config=types.GenerateContentConfig(
            response_schema=Event, response_mime_type="application/json"
        ),
    )
    assert result.parsed.day == date(2026, 10, 22)


def test_explicit_model_fallback_never_switches_models(backend):
    class Unavailable(Exception):
        status_code = 503

    service, client = backend
    service.router.errors = [Unavailable("private") for _ in range(3)] + [None]
    result = client.models.generate_content(model="gemini-a", contents="Hello")
    assert result.model_version == "gemini-a"
    assert [key for key, _ in service.router.calls] == [service.deployments[0].id] * 3 + [
        service.deployments[2].id
    ]
