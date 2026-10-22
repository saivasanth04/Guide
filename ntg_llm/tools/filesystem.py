"""UTF-8 filesystem capabilities bound to an explicit trusted local workspace."""

import heapq
import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Literal
from threading import RLock

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr

from ..errors import RoutingError
from .models import ToolError, ToolSpec
from .registry import ToolRegistry

_PROTECTED = {
    "beatapi.txt",
    ".git",
    ".venv",
    "__pycache__",
    ".ssh",
    ".aws",
    ".config",
    ".codex",
    ".agents",
}


class PathArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: StrictStr


class WriteArguments(PathArguments):
    content: StrictStr
    overwrite: StrictBool = False


class ReadData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str
    bytes_read: int


class WriteData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    bytes_written: int
    created: bool


class FileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: Literal["file", "directory"]


class ListData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    entries: list[FileEntry]
    truncated: bool


@contextmanager
def _filesystem_errors():
    try:
        yield
    except FileNotFoundError:
        raise ToolError("not_found") from None
    except FileExistsError:
        raise ToolError("already_exists") from None
    except PermissionError:
        raise ToolError("path_denied") from None
    except UnicodeError:
        raise ToolError("unsupported_encoding") from None
    except OSError:
        raise ToolError("io_error") from None


@dataclass(frozen=True)
class FilesystemTools:
    root: Path
    allow_overwrite: bool = False
    max_read_bytes: int = 262144
    max_write_bytes: int = 262144
    max_list_entries: int = 1000
    _lock: object = field(default_factory=RLock, init=False, repr=False, compare=False)

    def __post_init__(self):
        if type(self.allow_overwrite) is not bool or any(
            type(value) is not int or value < 1
            for value in (self.max_read_bytes, self.max_write_bytes, self.max_list_entries)
        ):
            raise RoutingError(400, "Invalid filesystem policy")
        try:
            if isinstance(self.root, str) and not self.root.strip():
                raise ValueError()
            supplied = PureWindowsPath(os.fspath(self.root))
            if supplied.drive.startswith("\\\\") or (supplied.drive and not supplied.root):
                raise ValueError()
            if any(self._protected(part) for part in supplied.parts):
                raise ValueError()
            root = Path(self.root).resolve(strict=True)
            if any(self._protected(part) for part in root.parts):
                raise ValueError()
            if not root.is_dir():
                raise ValueError()
        except (OSError, ValueError, TypeError, RuntimeError):
            raise RoutingError(400, "Workspace must be an existing directory") from None
        object.__setattr__(self, "root", root)

    @staticmethod
    def _protected(part):
        lower = part.casefold()
        return (
            lower in _PROTECTED
            or lower.startswith((".env", "._ntg_tmp_"))
            or ".sqlite3" in lower
            or lower.endswith(".db")
            or ".db-" in lower
        )

    @staticmethod
    def _check_info(info):
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)
        ):
            raise ToolError("path_denied")

    def _target(self, path: str, *, allow_root=False) -> Path:
        if not isinstance(path, str) or not path:
            raise ToolError("invalid_arguments")
        windows = PureWindowsPath(path)
        if windows.drive or windows.root or any(ord(c) < 32 or c in '<>:"|?*' for c in path):
            raise ToolError("path_denied")
        parts = path.replace("\\", "/").split("/")
        if ".." in parts:
            raise ToolError("path_denied")
        parts = [part for part in parts if part not in ("", ".")]
        if not parts and not (allow_root and path == "."):
            raise ToolError("path_denied")
        for part in parts:
            lower = part.casefold()
            base = lower.split(".")[0]
            if (
                self._protected(part)
                or part.endswith((".", " "))
                or base in {"con", "prn", "aux", "nul", "conin$", "conout$"}
                or re.fullmatch(r"(?:com|lpt)[0-9¹²³]", base)
            ):
                raise ToolError("path_denied")
        self._check_info(self.root.lstat())
        current = self.root
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                break
            self._check_info(info)
            if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise ToolError("path_denied")
        target = self.root.joinpath(*parts)
        if not target.resolve().is_relative_to(self.root):
            raise ToolError("path_denied")
        return target

    def _relative(self, target):
        return target.relative_to(self.root).as_posix()

    def snapshot(self, resource_paths, destination, *, max_files=1000, max_total_bytes=10485760):
        """Copy checked UTF-8 Python/resources to a fresh host staging directory."""
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.root) or self.root.is_relative_to(destination):
            raise ToolError("path_denied")
        resources = {self._relative(self._target(path)) for path in resource_paths}
        manifest, total, visited = {}, 0, 0
        pending = [self.root]
        with self._lock, _filesystem_errors():
            while pending:
                parent = pending.pop()
                for child in parent.iterdir():
                    visited += 1
                    if visited > max_files * 10:
                        raise ToolError("too_large")
                    if self._protected(child.name):
                        continue
                    name = self._relative(child)
                    checked = self._target(name)
                    info = checked.lstat()
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(checked)
                    elif stat.S_ISREG(info.st_mode) and (
                        child.suffix.lower() == ".py" or name in resources
                    ):
                        data = self.read_file(PathArguments(path=name)).content.encode("utf-8")
                        total += len(data)
                        if len(manifest) >= max_files or total > max_total_bytes:
                            raise ToolError("too_large")
                        target = destination / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        manifest[name] = hashlib.sha256(data).hexdigest()
            if not resources <= manifest.keys():
                raise ToolError("not_found")
        return dict(sorted(manifest.items()))

    def read_file(self, arguments: PathArguments) -> ReadData:
        with self._lock, _filesystem_errors():
            target = self._target(arguments.path)
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ToolError("path_denied")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(target, flags), "rb") as handle:
                opened = os.fstat(handle.fileno())
                self._check_info(opened)
                if not stat.S_ISREG(opened.st_mode):
                    raise ToolError("path_denied")
                data = handle.read(self.max_read_bytes + 1)
            if len(data) > self.max_read_bytes:
                raise ToolError("too_large")
            return ReadData(
                path=self._relative(target), content=data.decode("utf-8"), bytes_read=len(data)
            )

    def list_files(self, arguments: PathArguments) -> ListData:
        with self._lock, _filesystem_errors():
            target = self._target(arguments.path, allow_root=True)
            if not target.is_dir():
                if not target.exists():
                    raise ToolError("not_found")
                raise ToolError("path_denied")

            def entries():
                for child in target.iterdir():
                    try:
                        checked = self._target(self._relative(child))
                        info = checked.lstat()
                        if stat.S_ISDIR(info.st_mode):
                            yield FileEntry(name=child.name, type="directory")
                        elif stat.S_ISREG(info.st_mode):
                            yield FileEntry(name=child.name, type="file")
                    except (ToolError, FileNotFoundError):
                        continue

            selected = heapq.nsmallest(
                self.max_list_entries + 1,
                entries(),
                key=lambda item: (item.name.casefold(), item.name),
            )
            return ListData(
                path=self._relative(target),
                entries=selected[: self.max_list_entries],
                truncated=len(selected) > self.max_list_entries,
            )

    def write_file(self, arguments: WriteArguments) -> WriteData:
        temporary = None
        with self._lock, _filesystem_errors():
            target = self._target(arguments.path)
            data = arguments.content.encode("utf-8")
            if len(data) > self.max_write_bytes:
                raise ToolError("too_large")
            self._check_destination(target, arguments.overwrite)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = self._target(arguments.path)
            try:
                fd, temporary = tempfile.mkstemp(prefix="._ntg_tmp_", dir=target.parent)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                target = self._target(arguments.path)
                created = self._check_destination(target, arguments.overwrite)
                if created:
                    os.link(temporary, target)
                else:
                    os.replace(temporary, target)
                return WriteData(
                    path=self._relative(target), bytes_written=len(data), created=created
                )
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass

    def _check_destination(self, target, overwrite):
        try:
            info = target.lstat()
        except FileNotFoundError:
            return True
        self._check_info(info)
        if not stat.S_ISREG(info.st_mode):
            raise ToolError("path_denied")
        if not (self.allow_overwrite and overwrite):
            raise ToolError("already_exists")
        return False


def create_filesystem_registry(root, **policy) -> ToolRegistry:
    """Bind explicit host policy; model requests cannot replace the workspace root."""
    tools = FilesystemTools(root, **policy)
    return ToolRegistry(
        [
            ToolSpec(
                "read_file", "Read a UTF-8 workspace file", PathArguments, ReadData, tools.read_file
            ),
            ToolSpec(
                "write_file",
                "Write a UTF-8 workspace file; overwrite needs host permission",
                WriteArguments,
                WriteData,
                tools.write_file,
            ),
            ToolSpec(
                "list_files",
                "List immediate workspace entries; use . for the root",
                PathArguments,
                ListData,
                tools.list_files,
            ),
        ]
    )
