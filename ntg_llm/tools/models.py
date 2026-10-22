"""Host-owned tool definitions and typed, sanitized execution observations."""

from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

DataT = TypeVar("DataT", bound=BaseModel)

ERROR_MESSAGES = {
    "unknown_tool": "The requested tool is not registered",
    "invalid_arguments": "Tool arguments do not match the registered contract",
    "path_denied": "The requested path is not permitted",
    "not_found": "The requested workspace entry does not exist",
    "already_exists": "The destination exists; replacement is not permitted",
    "too_large": "The requested file exceeds the configured payload bound",
    "unsupported_encoding": "The file must contain UTF-8 text",
    "io_error": "The filesystem operation could not be completed",
}


class ToolError(Exception):
    """Expected failure: expose only a fixed code and message."""

    def __init__(self, code: str):
        self.code = code if code in ERROR_MESSAGES else "io_error"
        super().__init__(ERROR_MESSAGES[self.code])


class ToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str


class ToolResult(BaseModel, Generic[DataT]):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    tool: str
    ok: bool
    data: DataT | None = None
    error: ToolFailure | None = None

    @model_validator(mode="after")
    def check_outcome(self):
        if self.ok and self.data is not None and self.error is None:
            return self
        if not self.ok and self.data is None and self.error is not None:
            return self
        raise ValueError("Tool outcome must contain either data or failure")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel] = field(repr=False)
