"""Google-style text inputs translated to the existing provider-neutral router."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

from .errors import RoutingError


class GenerationParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: float | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    seed: int | None = None
    stop_sequences: list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    reasoning_effort: Literal["low", "medium", "high"] | None = None


def _object(value):
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True, mode="python")
    return value


def _part(value):
    value = _object(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value) == {"text"} and isinstance(value["text"], str):
        return value["text"]
    raise RoutingError(400, "generate_content currently supports text parts only")


def content_messages(contents):
    """Accept text, text parts, and user/model Content dicts or SDK objects."""
    items = contents if isinstance(contents, list) else [contents]
    messages, pending = [], []

    def flush():
        if pending:
            messages.append({"role": "user", "content": "\n".join(pending)})
            pending.clear()

    for value in items:
        value = _object(value)
        if isinstance(value, dict) and "parts" in value:
            flush()
            if (
                set(value) - {"parts", "role"}
                or value.get("role", "user") not in ("user", "model")
                or not isinstance(value["parts"], list)
                or not value["parts"]
            ):
                raise RoutingError(400, "Use user/model contents with nonempty text parts")
            messages.append(
                {
                    "role": "assistant" if value.get("role") == "model" else "user",
                    "content": "\n".join(_part(part) for part in value["parts"]),
                }
            )
        else:
            pending.append(_part(value))
    flush()
    if not messages or any(not message["content"].strip() for message in messages):
        raise RoutingError(400, "contents must contain nonempty text")
    return messages


def generation_request(contents, config):
    messages = content_messages(contents)
    config = {} if config is None else _object(config)
    if not isinstance(config, dict):
        raise RoutingError(400, "config must be a dictionary or GenerateContentConfig")
    config = {k: v for k, v in config.items() if v is not None}
    allowed = set(GenerationParameters.model_fields) | {
        "system_instruction",
        "response_schema",
        "response_mime_type",
        "candidate_count",
    }
    if set(config) - allowed:
        raise RoutingError(400, "Unsupported generate_content config option for this text router")
    if "candidate_count" in config and (
        type(config["candidate_count"]) is not int or config["candidate_count"] != 1
    ):
        raise RoutingError(400, "The router supports one candidate per completion")
    instruction = config.get("system_instruction")
    if instruction is not None:
        instruction = _object(instruction)
        if isinstance(instruction, dict) and instruction.get("role") == "system":
            instruction = {**instruction, "role": "user"}
        parts = content_messages(instruction)
        if any(part["role"] != "user" for part in parts):
            raise RoutingError(400, "system_instruction must contain text")
        messages.insert(0, {"role": "system", "content": "\n".join(p["content"] for p in parts)})
    response_model = config.get("response_schema")
    if response_model is not None and (
        not isinstance(response_model, type) or not issubclass(response_model, BaseModel)
    ):
        raise RoutingError(400, "response_schema must be a Pydantic BaseModel class")
    mime = config.get("response_mime_type")
    if mime not in (None, "text/plain", "application/json") or (
        mime == "text/plain" and response_model
    ):
        raise RoutingError(
            400, "Use text/plain or application/json with a compatible response_schema"
        )
    try:
        parameters = GenerationParameters.model_validate(
            {k: v for k, v in config.items() if k in GenerationParameters.model_fields}
        ).model_dump(exclude_none=True)
    except ValidationError:
        raise RoutingError(400, "Invalid generate_content generation parameters") from None
    rename = {"max_output_tokens": "max_tokens", "stop_sequences": "stop"}
    options = {rename.get(k, k): v for k, v in parameters.items()}
    if "top_k" in options:
        if not options["top_k"].is_integer():
            raise RoutingError(400, "top_k must be a positive whole number")
        options["top_k"] = int(options["top_k"])
    if mime == "application/json" and response_model is None:
        options["response_format"] = {"type": "json_object"}
    return messages, response_model, options
