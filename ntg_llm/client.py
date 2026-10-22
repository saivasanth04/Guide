"""Google-style generation and structured chat over the existing local router."""

import json
from pathlib import Path
from types import SimpleNamespace
import threading

from .settings import load_service
from .errors import RoutingError
from .schemas import Answer
from .responses import ChatCompletion, CompletionChoice, CompletionMessage
from .responses import GenerateContentResponse
from .generation import generation_request

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


class _Models:
    def __init__(self, service=None):
        self._service = service

    def generate_content(self, *, model, contents, config=None):
        if not isinstance(model, str) or not model.strip():
            raise RoutingError(400, "model must be a nonempty configured model name or router")
        messages, response_model, options = generation_request(contents, config)
        service = self._service if self._service is not None else _get_service()
        result = service.generate_content(
            model=model,
            messages=messages,
            response_model=response_model,
            generation_options=options,
        )
        if response_model is not None:
            payload = {"answer": result["answer"]} if response_model is Answer else result["data"]
            parsed = response_model.model_validate_json(json.dumps(payload), strict=True)
            text = parsed.model_dump_json()
        else:
            text = result["answer"]
            parsed = json.loads(text) if options.get("response_format") else None
        return GenerateContentResponse(
            text=text,
            parsed=parsed,
            model_version=result["diagnostics"]["selected_model"],
            diagnostics=result["diagnostics"],
        )


class LLMClient:
    """Credentials load lazily from .env; construction makes no provider calls.

    models.generate_content accepts Google-style text inputs and generation config.
    chat.completions.create retains the existing structured completion interface.
    """

    def __init__(self, *, service=None):
        self.chat = SimpleNamespace(completions=_ChatCompletions(service))
        self.models = _Models(service)


# Backward-compatible name for existing application imports.
ChatClient = LLMClient
