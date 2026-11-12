"""Shared workflow state and structured agent outcomes; no orchestration imports."""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from typing_extensions import TypedDict


def validate_plan(plan):
    filename, markdown = plan["filename"], plan["markdown"]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*-plan\.md", filename):
        raise ValueError(f"Invalid plan filename: {filename!r}")
    if not re.search(r"^# .+\n", markdown, re.M) or not re.search(r"^## .+", markdown, re.M):
        raise ValueError("Expected a Markdown plan with a title and sections.")


class ResearchRequest(BaseModel):
    """Return only when additional evidence is needed before proceeding."""

    question: str = Field(min_length=1, max_length=2000)
    context: str = Field(description="Relevant discoveries and exact uncertainty", max_length=6000)


class Source(BaseModel):
    location: str = Field(description="Actual fetched URL or absolute repository file path")
    evidence: str = Field(description="Specific supporting fact, version and locator", max_length=3000)


class ResearchReport(BaseModel):
    """Actionable evidence for the requesting Planner or Coder, not another plan."""

    findings: str = Field(max_length=12000)
    sources: list[Source] = Field(max_length=20)
    recommendations: list[str] = Field(max_length=15)
    limitations: list[str] = Field(max_length=15)


class DevelopmentPlan(BaseModel):
    """A feasible plan following the existing planning skill, ready for Coder."""

    filename: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*-plan\.md$")
    markdown: str = Field(min_length=20, max_length=50000)
    verification_commands: list[str] = Field(min_length=1, max_length=15)

    @model_validator(mode="after")
    def valid_artifact(self):
        validate_plan(self.model_dump())
        return self


class ImplementationResult(BaseModel):
    """Report implementation status. The runtime independently verifies execution."""

    status: Literal["completed", "blocked", "failed"]
    summary: str = Field(max_length=6000)
    changed_files: list[str] = Field(max_length=100)
    issues: list[str] = Field(max_length=30)


class Blocked(BaseModel):
    """Return when necessary evidence, access, requirements, or budget is unavailable."""

    reason: str = Field(min_length=1, max_length=6000)


class WorkflowState(TypedDict, total=False):
    task: str
    workspace: str
    phase: Literal["planner", "coder"]
    plan: dict
    plan_path: str
    request: dict | None
    research: dict[str, dict]
    research_calls: int
    research_messages: list
    research_events: list[dict]
    research_feedback: str
    feedback: str
    handoffs: int
    planner_messages: list
    coder_messages: list
    tool_events: list[dict]
    verification: list[dict]
    result: dict
    status: Literal["running", "completed", "blocked", "failed"]


