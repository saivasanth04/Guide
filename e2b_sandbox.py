from __future__ import annotations

import json
import os
import shlex
import warnings
from pathlib import Path

from dotenv import load_dotenv
from e2b import CommandExitException, NotFoundException, Sandbox
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class SandboxCommandInput(BaseModel):
    command: str = Field(
        description=(
            "Shell command to execute inside the E2B sandbox. "
            "Examples: 'python app.py', 'pytest -q', "
            "'npm test', 'g++ main.cpp -o main && ./main'."
        )
    )

    timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=300,
        description="Maximum execution time for the command.",
    )


class E2BSandboxExecutor:
    """
    Maintains one E2B sandbox for a coding-agent session.

    Local source files are synchronized into the remote sandbox before
    every command. The same sandbox is reused so installed dependencies
    and build artifacts can survive between commands.
    """

    DEFAULT_REMOTE_WORKSPACE = "/home/user/workspace"

    IGNORED_DIRECTORIES = {
        ".git",
        ".idea",
        ".vscode",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "dist",
        "build",
        "target",
    }

    IGNORED_FILES = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
    }

    MAX_FILE_SIZE = 5 * 1024 * 1024

    def __init__(
        self,
        workspace: str | Path,
        *,
        remote_workspace: str = DEFAULT_REMOTE_WORKSPACE,
        sandbox_timeout: int = 900,
        template: str | None = None,
    ):
        self.workspace = Path(workspace).resolve()

        if not self.workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {self.workspace}")

        self.remote_workspace = remote_workspace
        self.sandbox_timeout = sandbox_timeout

        load_dotenv(Path(__file__).resolve().parent / ".env")
        self.template = template or os.getenv("E2B_TEMPLATE") or None

        self._sandbox: Sandbox | None = None

        # Files previously uploaded by us.
        #
        # This lets us remove source files deleted locally while preserving
        # remote-only files such as node_modules, .venv or compiler output.
        self._synced_paths: set[str] = set()

    # --------------------------------------------------
    # Sandbox lifecycle
    # --------------------------------------------------

    def _get_sandbox(self) -> Sandbox:
        if self._sandbox is not None:
            return self._sandbox

        if not os.getenv("E2B_API_KEY"):
            raise RuntimeError("E2B_API_KEY is missing. Add E2B_API_KEY to your .env file.")

        if self.template:
            self._sandbox = Sandbox.create(
                self.template,
                timeout=self.sandbox_timeout,
            )
        else:
            self._sandbox = Sandbox.create(
                timeout=self.sandbox_timeout,
            )

        try:
            self._sandbox.files.make_dir(self.remote_workspace)
        except Exception:
            self.close()
            raise

        return self._sandbox

    # --------------------------------------------------
    # Workspace synchronization
    # --------------------------------------------------

    def _should_ignore(self, path: Path) -> bool:
        relative = path.relative_to(self.workspace)

        if any(part in self.IGNORED_DIRECTORIES for part in relative.parts):
            return True

        if path.name in self.IGNORED_FILES:
            return True

        # Never upload environment files containing credentials.
        if path.name.startswith(".env"):
            return True

        try:
            if path.stat().st_size > self.MAX_FILE_SIZE:
                return True
        except OSError:
            return True

        return False

    def _workspace_files(self) -> list[Path]:
        files: list[Path] = []

        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue

            if path.is_symlink():
                continue

            if not path.resolve().is_relative_to(self.workspace):
                continue

            if self._should_ignore(path):
                continue

            files.append(path)

        return files

    def sync_workspace(self) -> int:
        sandbox = self._get_sandbox()

        current_paths: set[str] = set()
        files = self._workspace_files()

        for local_path in files:
            relative = local_path.relative_to(self.workspace)

            relative_posix = relative.as_posix()

            remote_path = f"{self.remote_workspace}/{relative_posix}"

            current_paths.add(relative_posix)

            sandbox.files.write(
                remote_path,
                local_path.read_bytes(),
            )

        # Remove files that the agent deleted locally.
        #
        # Do NOT wipe the whole remote directory because doing that would
        # destroy npm packages, virtual environments and compiler outputs
        # installed during previous execution calls.
        deleted_paths = self._synced_paths - current_paths

        for relative_path in deleted_paths:
            remote_path = f"{self.remote_workspace}/{relative_path}"

            sandbox.commands.run(f"rm -f -- {shlex.quote(remote_path)}")

        self._synced_paths = current_paths

        return len(files)

    # --------------------------------------------------
    # Command execution
    # --------------------------------------------------

    def execute(
        self,
        command: str,
        timeout_seconds: int = 120,
    ) -> str:
        sandbox = None
        synced_files = 0
        try:
            sandbox = self._get_sandbox()
            synced_files = self.sync_workspace()
            result = sandbox.commands.run(
                command,
                cwd=self.remote_workspace,
                timeout=timeout_seconds,
            )

            exit_code = getattr(result, "exit_code", 0)
            stdout = getattr(result, "stdout", "")
            stderr = getattr(result, "stderr", "")

        except CommandExitException as error:
            exit_code = getattr(error, "exit_code", 1)
            stdout = getattr(error, "stdout", "")
            stderr = getattr(error, "stderr", str(error))

        except Exception as error:
            if isinstance(error, NotFoundException):
                # The session expired or was removed. The next call can recreate it.
                self._sandbox = None
                self._synced_paths.clear()
            return json.dumps(
                {
                    "success": False,
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "error": str(error)
                    if isinstance(error, RuntimeError)
                    else type(error).__name__,
                    "command": command,
                    "workspace": self.remote_workspace,
                    "synced_files": synced_files,
                }
            )

        payload = {
            "success": exit_code == 0,
            "exit_code": exit_code,
            "stdout": self._truncate(stdout),
            "stderr": self._truncate(stderr),
            "command": command,
            "workspace": self.remote_workspace,
            "synced_files": synced_files,
            "sandbox_id": sandbox.sandbox_id,
        }

        return json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )

    @staticmethod
    def _truncate(
        value: str | None,
        limit: int = 20_000,
    ) -> str:
        value = value or ""

        if len(value) <= limit:
            return value

        return value[:limit] + "\n\n[output truncated]"

    # --------------------------------------------------
    # LangChain tool
    # --------------------------------------------------

    def as_tool(self) -> StructuredTool:
        return StructuredTool.from_function(
            func=self.execute,
            name="execute_in_sandbox",
            description=(
                "Execute build, test, lint, compile, or run commands "
                "inside an isolated E2B Linux sandbox. "
                "The current coding workspace is synchronized into "
                "the sandbox automatically before execution. "
                f"Commands start in {self.remote_workspace}; use relative project paths "
                "or Linux paths in that environment, not local host paths. "
                "The sandbox is reused during this session, but remote changes "
                "are not copied back locally. Persist source edits through the "
                "filesystem tools. Environment files, ignored directories, and "
                "files over 5 MiB are excluded from synchronization. "
                "Use this tool to verify that code actually works. "
                "Inspect stdout, stderr and exit_code, fix failures, "
                "and execute again when necessary."
            ),
            args_schema=SandboxCommandInput,
        )

    # --------------------------------------------------
    # Cleanup
    # --------------------------------------------------

    def close(self) -> None:
        if self._sandbox is None:
            return

        try:
            self._sandbox.kill()
        except Exception as error:
            warnings.warn(f"E2B sandbox cleanup failed: {type(error).__name__}", RuntimeWarning)
        finally:
            self._sandbox = None
            self._synced_paths.clear()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
