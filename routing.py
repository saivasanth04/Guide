"""Compatibility exports for applications using the former flat layout."""

from ntg_llm.defaults import MODELS, LEGACY_REPLACEMENTS, ENFORCEMENT_REASONS
from ntg_llm.errors import RoutingError
from ntg_llm.schemas import Answer, Deployment
from ntg_llm.store import RotationStore
from ntg_llm.strategy import PersistentRoundRobin
from ntg_llm.service import CompletionService
from ntg_llm.settings import load_service

__all__ = [
    "MODELS",
    "LEGACY_REPLACEMENTS",
    "ENFORCEMENT_REASONS",
    "RoutingError",
    "Answer",
    "Deployment",
    "RotationStore",
    "PersistentRoundRobin",
    "CompletionService",
    "load_service",
]
