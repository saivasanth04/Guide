"""Small OpenAI-style interface over the local router; not an HTTP API server."""

import json
from pathlib import Path
from types import SimpleNamespace
import threading

from .settings import load_service
from .errors import RoutingError
from .schemas import Answer
from .responses import ChatCompletion, CompletionChoice, CompletionMessage

_service = None
_lock = threading.Lock()


def _get_service():
    global _service
    with _lock:
        if _service is None:
            _service = load_service(Path(__file__).resolve().parent.parent / ".env")
    return _service


def structured_completion(prompt, *, response_model=Answer):
    """Return a validated answer dictionary with sanitized routing diagnostics."""
    return _get_service().structured_completion(prompt, response_model=response_model)


class _ChatCompletions:
    def __init__(self, service=None):
        self._service = service

    def create(self, *, model="router", messages, response_model=Answer):
        if model not in ("router", "structured"):
            raise RoutingError(
                400, 'Use model="router"; deployment selection belongs in local LLM configuration'
            )
        service = self._service if self._service is not None else _get_service()
        result = service.chat_completion(messages, response_model=response_model)
        payload = {"answer": result["answer"]} if response_model is Answer else result["data"]
        parsed = response_model.model_validate_json(json.dumps(payload), strict=True)
        return ChatCompletion[response_model](
            model=result["diagnostics"]["selected_model"],
            choices=[
                CompletionChoice[response_model](
                    index=0,
                    message=CompletionMessage[response_model](
                        role="assistant",
                        parsed=parsed,
                        content=result["answer"]
                        if response_model is Answer
                        else parsed.model_dump_json(),
                    ),
                    finish_reason="stop",
                )
            ],
            diagnostics=result["diagnostics"],
        )


class LLMClient:
    """Credentials load lazily from .env; construction makes no provider calls.

    Supports text-only, non-streaming completions through the router alias.
    """

    def __init__(self, *, service=None):
        self.chat = SimpleNamespace(completions=_ChatCompletions(service))


# Backward-compatible name for existing application imports.
ChatClient = LLMClient
