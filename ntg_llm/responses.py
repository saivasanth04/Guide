"""Typed, JSON-serializable chat responses for application boundaries."""

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class CompletionMessage(BaseModel, Generic[ResponseModel]):
    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str
    parsed: ResponseModel


class CompletionChoice(BaseModel, Generic[ResponseModel]):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    message: CompletionMessage[ResponseModel]
    finish_reason: Literal["stop"] = "stop"


class ChatCompletion(BaseModel, Generic[ResponseModel]):
    """Validated output plus routing metadata; serialize with model_dump_json()."""

    model_config = ConfigDict(extra="forbid")

    model: str
    choices: list[CompletionChoice[ResponseModel]]
    diagnostics: dict[str, Any]


class GenerateContentResponse(BaseModel):
    """Text-generation result with the actual routed model and safe diagnostics."""

    model_config = ConfigDict(extra="forbid")
    text: str
    model_version: str
    parsed: Any = None
    diagnostics: dict[str, Any]
