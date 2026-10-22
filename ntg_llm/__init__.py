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
    "run_verified_project",
    "create_tester_agent",
    "ExecutionPolicy",
    "TestProfile",
    "ProjectRunResult",
]


def __getattr__(name):
    if name == "run_verified_project":
        from .project_workflow import run_verified_project

        return run_verified_project
    if name == "create_tester_agent":
        from .tester import create_tester_agent

        return create_tester_agent
    if name in {"ExecutionPolicy", "TestProfile", "ProjectRunResult"}:
        from . import testing_models

        return getattr(testing_models, name)
    raise AttributeError(name)
