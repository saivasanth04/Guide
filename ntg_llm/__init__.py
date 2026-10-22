"""Public provider-neutral structured completion API."""

from .client import LLMClient, ChatClient, structured_completion
from .errors import RoutingError as LLMError
from .schemas import Answer
from .cli import cli_entrypoint
from .responses import ChatCompletion
from .agent import Agent, AgentResult
from .coder import CodeOutput, create_coder_agent
from .tools.registry import ToolRegistry
from .tools.filesystem import create_filesystem_registry

__all__ = [
    "LLMClient",
    "ChatClient",
    "LLMError",
    "Answer",
    "structured_completion",
    "cli_entrypoint",
    "ChatCompletion",
    "Agent",
    "AgentResult",
    "CodeOutput",
    "create_coder_agent",
    "ToolRegistry",
    "create_filesystem_registry",
]
