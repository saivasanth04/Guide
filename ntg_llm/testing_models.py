"""Host-owned contracts for project execution and bounded repair."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ExecutionPolicy(Contract):
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    control_timeout: int = Field(default=10, ge=1, le=60)
    output_bytes: int = Field(default=32768, ge=256, le=1048576)
    memory_mb: int = Field(default=256, ge=64, le=4096)
    pids: int = Field(default=64, ge=8, le=256)
    tmp_mb: int = Field(default=64, ge=1, le=256)
    max_files: int = Field(default=1000, ge=1, le=10000)
    max_total_bytes: int = Field(default=10485760, ge=1)
    max_file_bytes: int = Field(default=262144, ge=1)


class TestProfile(Contract):
    __test__ = False
    kind: Literal["script", "unittest"]
    entrypoint: str | None = None
    tests_root: str | None = None
    resource_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def matching_profile(self):
        if self.kind == "script":
            valid = bool(self.entrypoint) and self.tests_root is None
        else:
            valid = bool(self.tests_root) and self.entrypoint is None
        if not valid:
            raise ValueError("Select exactly one script or external unittest directory")
        return self


class ExecutionReport(Contract):
    run_id: str
    snapshot_id: str
    kind: Literal["script", "unittest"]
    outcome: Literal[
        "passed",
        "failed",
        "timeout",
        "output_limit",
        "resource_limit",
        "no_tests",
        "infrastructure_error",
    ]
    exit_code: int | None = None
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    tests_run: int | None = None
    cleanup_confirmed: bool = True
    error_code: str | None = None


class TesterFeedback(Contract):
    __test__ = False
    summary: str
    evidence_fields: list[str]
    suggested_changes: list[str]


class ProjectRunResult(Contract):
    status: Literal["passed", "repair_exhausted", "blocked"]
    repairs_used: int
    coder_result: dict | None
    reports: list[ExecutionReport]
    feedback: list[TesterFeedback]
    diagnostics: dict
