"""Public provider-neutral structured completion API."""

from .client import LLMClient, ChatClient, structured_completion
from .errors import RoutingError as LLMError
from .schemas import Answer
from .cli import cli_entrypoint
from .responses import ChatCompletion, GenerateContentResponse

__all__ = [
    "LLMClient",
    "ChatClient",
    "LLMError",
    "Answer",
    "structured_completion",
    "cli_entrypoint",
    "ChatCompletion",
    "GenerateContentResponse",
]
