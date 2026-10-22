"""Compatibility imports; new applications can import ntg_llm."""

from ntg_llm import LLMClient, LLMError, structured_completion, cli_entrypoint

__all__ = ["LLMClient", "LLMError", "structured_completion", "cli_entrypoint"]
