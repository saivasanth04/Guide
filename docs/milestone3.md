# Milestone 3 — Structured LLM output

The application defines a Python response model. The LLM supplies JSON that
matches that model. The routing layer validates it before returning it to the
application. This is a general output contract, independent of the prompt's topic.

Pydantic models provide Python types, JSON Schema generation, validation, and JSON
serialization. A task list, classifier result, extraction result, or agent message
can each define its own model. No task planner, task execution, or agent runtime
is introduced by this milestone.

## Application flow

1. Choose a Pydantic response model; the default `Answer` has one `answer` field.
2. Call `client.chat.completions.create(messages=..., response_model=YourModel)`.
3. The router sends the model's JSON schema to the selected provider. For an
   explicitly configured JSON-object deployment, the schema is a system instruction.
4. Local validation checks the JSON structure and strict Python field types.
5. Read the typed object from `response.choices[0].message.parsed`.
6. Serialize the result with `parsed.model_dump_json()` or serialize the complete
   response, including safe diagnostics, with `response.model_dump_json()`.

`ChatCompletion`, its choices, and its messages are Pydantic models too. Structured
data and diagnostics therefore remain serializable at application boundaries.
The system currently makes one completion at a time; agent communication itself
is outside this milestone.

## General-purpose contract example

```python
from typing import Literal

from pydantic import BaseModel

from ntg_llm import LLMClient


class Classification(BaseModel):
    category: Literal["positive", "negative", "neutral"]
    confidence: float


response = LLMClient().chat.completions.create(
    messages=[{"role": "user", "content": "Classify: I enjoyed this book."}],
    response_model=Classification,
)
result = response.choices[0].message.parsed
print(result.category)
print(result.model_dump_json())
```

This is an illustration of a caller's contract, not built-in classification
behavior. A different model changes the output shape for that call.

## Validation and failure behavior

- Named, nested models, lists, enums, numbers, strings, and nullable fields are
  supported. Open dictionaries are rejected; declare their fields in nested models.
- Every declared field must appear in the generated strict contract, including
  fields with Python defaults. Nullable fields can explicitly contain `null`.
- Unknown or duplicate fields, missing fields, incorrect types, invalid enum
  values, non-finite numbers, free text, and Markdown-wrapped JSON are rejected.
- Invalid input/schema returns a sanitized 400 before selecting a deployment.
- Invalid generated output returns a sanitized 502 without retry or fallback.
  The system does not guess missing fields or repair invalid responses silently.
- Upstream model support for schema features varies. JSON-object mode cannot
  enforce the complete schema remotely, so local validation remains mandatory.
- Default `message.content` remains answer text for existing callers; structured
  consumers use `message.parsed`. Custom-schema content is serialized JSON.

The existing account/model rotation, retries for transient failures, safe failure
diagnostics, local secrets, and persistent state continue to apply. No assumed
local quota limits are introduced.

## Offline acceptance checks

Run `python -m pytest -q`, `python -m ruff check .`, and
`python -m ruff format --check .`.

Tests cover typed default output, unrelated caller-defined contracts, nested
objects and lists, full-response JSON serialization, invalid JSON/schema handling,
concurrent requests, rotation, persistence, provider adapters, and retry behavior.
The shared test fixture blocks live HTTP; test providers return synthetic data.
