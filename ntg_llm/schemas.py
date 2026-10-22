"""Validated response contracts and deployment metadata."""

import json
from dataclasses import dataclass, field
from pydantic import BaseModel, ConfigDict, StrictStr


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: StrictStr


@dataclass
class Deployment:
    key_label: str
    project_id: str | None
    model: str
    api_key: str = field(repr=False)
    provider: str = "gemini"
    account_id: str | None = None
    response_mode: str = "json_schema"
    api_base: str | None = field(default=None, repr=False)
    account_label: str | None = None

    @property
    def backend(self):
        return f"{self.provider}/{self.model}"

    @property
    def id(self):
        scope = self.account_id or self.project_id or self.key_label
        identity = (
            [scope, self.model] if self.provider == "gemini" else [self.provider, scope, self.model]
        )
        return json.dumps(identity, separators=(",", ":"))

    @property
    def diagnostics(self):
        return dict(
            provider=self.provider,
            key_label=self.key_label,
            account_label=self.account_label or self.key_label,
            project_id=self.project_id,
            selected_model=self.model,
        )


def response_schema(response_model):
    """Build a closed schema before selecting or calling any deployment.

    Object fields must be declared in Pydantic models. Open-ended mappings
    cannot provide a predictable field contract and are rejected explicitly.
    """
    from .errors import RoutingError

    if not isinstance(response_model, type) or not issubclass(response_model, BaseModel):
        raise RoutingError(400, "response_model must be a Pydantic BaseModel class")
    try:
        schema = response_model.model_json_schema()
        if schema.get("type") != "object":
            raise ValueError("Object model required")

        def close(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    if "properties" not in node or node.get("additionalProperties") not in (
                        None,
                        False,
                    ):
                        raise ValueError("Declare object fields using a nested Pydantic model")
                    node["additionalProperties"] = False
                    node["required"] = list(node["properties"])
                node.pop("default", None)
                for keyword in ("properties", "$defs", "definitions", "patternProperties"):
                    for value in node.get(keyword, {}).values():
                        close(value)
                for keyword in ("items", "additionalProperties", "not"):
                    close(node.get(keyword))
                for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
                    for value in node.get(keyword, []):
                        close(value)

        close(schema)
        return schema
    except Exception:
        raise RoutingError(
            400,
            "response_model must describe named JSON object fields; use nested models instead of open dictionaries",
        ) from None


def validate_response(content, response_model, schema):
    """Reject non-JSON, duplicate fields, extras, missing fields and wrong types."""
    from jsonschema import Draft202012Validator

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    value = json.loads(
        content,
        object_pairs_hook=unique_fields,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number")),
    )
    Draft202012Validator(schema).validate(value)
    parsed = response_model.model_validate_json(content, strict=True)
    if isinstance(parsed, Answer) and not parsed.answer.strip():
        raise ValueError("Empty answer")
    return parsed
