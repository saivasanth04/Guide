"""Schema contracts are enforced with mocked providers and no live requests."""

import json
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

import pytest
from pydantic import BaseModel

from ntg_llm import LLMClient, LLMError
from ntg_llm.schemas import response_schema
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore
from tests.helpers import FakeRouter, deployments


class Language(str, Enum):
    python = "python"


class Program(BaseModel):
    language: Language
    code: str


class ProgramResponse(BaseModel):
    answer: str
    program: Program


@pytest.fixture
def service(tmp_path, monkeypatch):
    import ntg_llm.client as client

    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.kwargs = kwargs
            return super().completion(**kwargs)

    service = CompletionService(
        deployments(), RotationStore(tmp_path / "state.db"), RecordingRouter
    )
    monkeypatch.setattr(client, "_service", service)
    return service


def test_custom_schema_sent_upstream_and_typed_result_returned(service):
    service.router.content = json.dumps(
        {
            "answer": "Here is the program",
            "program": {"language": "python", "code": "print(1)"},
        }
    )
    response = LLMClient().chat.completions.create(
        messages=[{"role": "user", "content": "write a program"}], response_model=ProgramResponse
    )
    parsed = response.choices[0].message.parsed
    assert isinstance(parsed, ProgramResponse)
    assert parsed.program.language is Language.python
    assert json.loads(response.choices[0].message.content)["program"]["code"] == "print(1)"
    schema = service.router.kwargs["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert schema["schema"]["$defs"]["Program"]["additionalProperties"] is False
    assert response.diagnostics["attempt_count"] == 1


@pytest.mark.parametrize(
    "content",
    [
        "Here is your answer",
        "{}",
        '{"answer": 7, "program": {"language":"python","code":"x"}}',
        '{"answer":"x","program":{"language":"python","code":"x","secret":"x"}}',
        '{"answer":"x","program":{"language":"javascript","code":"x"}}',
        '{"answer":"x","answer":"y","program":{"language":"python","code":"x"}}',
    ],
)
def test_invalid_custom_output_has_one_attempt_no_retry_or_fallback(service, content):
    service.router.content = content
    with pytest.raises(LLMError) as error:
        service.structured_completion("hi", response_model=ProgramResponse)
    assert error.value.status_code == 502
    assert len(service.router.calls) == 1
    assert content not in json.dumps(error.value.as_dict())


def test_json_object_mode_includes_custom_schema_and_still_validates(service):
    service.deployments[0].response_mode = "json_object"
    service.router.content = '{"answer":"x","program":{"language":"python","code":"x"}}'
    result = service.structured_completion("hi", response_model=ProgramResponse)
    assert result["data"]["program"]["code"] == "x"
    assert service.router.kwargs["response_format"] == {"type": "json_object"}
    assert "Program" in service.router.kwargs["messages"][0]["content"]


def test_invalid_schema_does_not_advance_rotation_or_call_provider(service):
    class OpenMapping(BaseModel):
        data: dict[str, str]

    for model in (dict, OpenMapping):
        with pytest.raises(LLMError) as error:
            service.structured_completion("hi", response_model=model)
        assert error.value.status_code == 400
    assert service.router.calls == []
    assert service.ledger.select(service.deployments) == service.deployments[0]


def test_schema_fields_named_default_are_preserved():
    class NamedFields(BaseModel):
        default: str
        properties: str

    assert set(response_schema(NamedFields)["properties"]) == {"default", "properties"}


def test_schema_contract_is_request_local_during_concurrency(service):
    class Other(BaseModel):
        answer: str

    models = [Other, type("Alternative", (BaseModel,), {"__annotations__": {"answer": str}})]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda index: service.structured_completion("hi", response_model=models[index % 2]),
                range(20),
            )
        )
    assert all(result["data"] == {"answer": "actual answer"} for result in results)
    assert len(service.router.calls) == 20


def test_default_response_is_typed_and_entire_envelope_is_serializable(service):
    from ntg_llm import Answer, ChatCompletion

    response = LLMClient(service=service).chat.completions.create(
        messages=[{"role": "user", "content": "Hello"}],
    )
    assert isinstance(response, ChatCompletion)
    assert isinstance(response.choices[0].message.parsed, Answer)
    serialized = json.loads(response.model_dump_json())
    assert serialized["choices"][0]["message"]["parsed"] == {"answer": "actual answer"}
    assert serialized["diagnostics"]["failed_attempts"] == []
    assert "mock-secret" not in response.model_dump_json()


def test_caller_chooses_unrelated_contracts_without_answer_or_task_fields(service):
    from typing import Literal

    class Classification(BaseModel):
        category: Literal["positive", "negative"]
        confidence: float

    class Person(BaseModel):
        name: str
        age: int

    class Extraction(BaseModel):
        people: list[Person]

    client = LLMClient(service=service)
    cases = [
        (Classification, {"category": "positive", "confidence": 0.9}),
        (Extraction, {"people": [{"name": "Ada", "age": 36}]}),
    ]
    for contract, output in cases:
        service.router.content = json.dumps(output)
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": "Return the requested information."}],
            response_model=contract,
        )
        parsed = response.choices[0].message.parsed
        assert isinstance(parsed, contract)
        assert parsed.model_dump(mode="json") == output
        assert json.loads(response.model_dump_json())["choices"][0]["message"]["parsed"] == output
        assert set(
            service.router.kwargs["response_format"]["json_schema"]["schema"]["properties"]
        ) == set(output)


def test_schema_rejects_free_text_even_when_wrapped_in_markdown(service):
    service.router.content = '```json\n{"answer":"hello"}\n```'
    with pytest.raises(LLMError) as error:
        LLMClient(service=service).chat.completions.create(
            messages=[{"role": "user", "content": "Hello"}],
        )
    assert error.value.status_code == 502
    assert len(service.router.calls) == 1
