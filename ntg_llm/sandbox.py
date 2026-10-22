"""Disposable Docker execution. No host-code execution or implicit image pulls."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Protocol
from uuid import uuid4

from .errors import RoutingError
from .testing_models import ExecutionReport
from .tools.filesystem import FilesystemTools
from .tools.models import ToolError


class SandboxRunner(Protocol):
    """Host-controlled execution seam; alternate backends must preserve these guarantees."""

    def preflight(self, workspace, profile): ...
    def snapshot(self, workspace, profile, destination): ...
    def run(self, workspace, profile, trusted_tests=None): ...


def snapshot_id(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def safe_text(data):
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)


class DockerSandboxRunner:
    def __init__(self, policy):
        self.policy = policy

    def _control(self, *args, check=True):
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            timeout=self.policy.control_timeout,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check and result.returncode:
            raise RoutingError(
                503, "Sandbox control operation failed", {"code": "sandbox_unavailable"}
            )
        return result

    def preflight(self, workspace, profile):
        fs = FilesystemTools(Path(workspace), max_read_bytes=self.policy.max_file_bytes)
        try:
            if profile.entrypoint:
                fs._target(profile.entrypoint)
                if not profile.entrypoint.endswith(".py"):
                    raise ValueError()
            for resource in profile.resource_paths:
                fs._target(resource)
            if profile.tests_root:
                tests = FilesystemTools(Path(profile.tests_root)).root
                if tests.is_relative_to(fs.root) or fs.root.is_relative_to(tests):
                    raise ValueError()
            # Environment overrides could silently direct local mounts to another host.
            if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
                raise ValueError()
            context = json.loads(self._control("context", "inspect").stdout)[0]
            endpoint = context["Endpoints"]["docker"]["Host"]
            if not endpoint.startswith(("npipe://", "unix://")):
                raise ValueError()
            if self._control("info", "--format", "{{.OSType}}").stdout.strip() != b"linux":
                raise ValueError()
            image = json.loads(self._control("image", "inspect", self.policy.image_id).stdout)[0]
            if image["Id"] != self.policy.image_id or image["Os"] != "linux":
                raise ValueError()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, ToolError):
            raise RoutingError(
                503, "Invalid or unavailable local Linux sandbox", {"code": "sandbox_unavailable"}
            ) from None

    def snapshot(self, workspace, profile, destination):
        fs = FilesystemTools(Path(workspace), max_read_bytes=self.policy.max_file_bytes)
        return fs.snapshot(
            profile.resource_paths,
            destination,
            max_files=self.policy.max_files,
            max_total_bytes=self.policy.max_total_bytes,
        )

    def run(self, workspace, profile, trusted_tests=None):
        """Snapshot, execute once, retain bounded evidence and always remove owned container."""
        run_id = "ntg-test-" + uuid4().hex
        started = time.monotonic()
        outcome, error, exit_code, tests_run = "infrastructure_error", None, None, None
        manifest, buffers = {}, [bytearray(), bytearray()]
        truncated = [False, False]
        overflow, ready = threading.Event(), threading.Event()
        marker = ("NTG_DONE_" + uuid4().hex).encode()
        proc, readers, attempted = None, [], False
        cleanup = True
        with tempfile.TemporaryDirectory(prefix="ntg-sandbox-") as temp:
            staging = Path(temp)
            code = staging / "code"
            code.mkdir()
            try:
                fs = FilesystemTools(Path(workspace), max_read_bytes=self.policy.max_file_bytes)
                entrypoint = (
                    fs._relative(fs._target(profile.entrypoint)) if profile.entrypoint else None
                )
                manifest = self.snapshot(workspace, profile, code)
                if entrypoint and entrypoint not in manifest:
                    outcome, error = "failed", "missing_entrypoint"
                else:
                    flags = [
                        "create",
                        "--name",
                        run_id,
                        "--network",
                        "none",
                        "--read-only",
                        "--user",
                        "65534:65534",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--pids-limit",
                        str(self.policy.pids),
                        "--memory",
                        f"{self.policy.memory_mb}m",
                        "--memory-swap",
                        f"{self.policy.memory_mb}m",
                        "--cpus",
                        "1",
                        "--tmpfs",
                        f"/tmp:rw,noexec,nosuid,size={self.policy.tmp_mb}m",
                        "--log-driver",
                        "none",
                        "--workdir",
                        "/workspace",
                        "--mount",
                        f"type=bind,source={code},target=/workspace,readonly",
                        "--entrypoint",
                        "python",
                    ]
                    if profile.kind == "unittest":
                        if trusted_tests is None:
                            raise ValueError("Missing frozen tests")
                        harness = Path(__file__).with_name("testing_harness.py").resolve()
                        flags += [
                            "--mount",
                            f"type=bind,source={trusted_tests},target=/tests,readonly",
                            "--mount",
                            f"type=bind,source={harness},target=/harness.py,readonly",
                        ]
                        command = [
                            "-B",
                            "/harness.py",
                            marker.decode(),
                            str(self.policy.timeout_seconds + self.policy.control_timeout + 1),
                        ]
                    else:
                        command = ["-B", "/workspace/" + entrypoint]
                    attempted = True
                    self._control(*flags, self.policy.image_id, *command)
                    proc = subprocess.Popen(
                        ["docker", "start", "--attach", run_id],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        stdin=subprocess.DEVNULL,
                        shell=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )

                    def drain(stream, index):
                        tail = b""
                        while chunk := stream.read1(4096):
                            tail = (tail + chunk)[-8192:]
                            if marker in tail:
                                ready.set()
                            room = self.policy.output_bytes - len(buffers[index])
                            buffers[index].extend(chunk[:room])
                            if len(chunk) > room:
                                truncated[index] = True
                                overflow.set()

                    for index, stream in enumerate((proc.stdout, proc.stderr)):
                        thread = threading.Thread(target=drain, args=(stream, index), daemon=True)
                        readers.append(thread)
                        thread.start()
                    deadline = time.monotonic() + self.policy.timeout_seconds
                    while proc.poll() is None and not ready.is_set() and not overflow.is_set():
                        if time.monotonic() >= deadline:
                            break
                        ready.wait(0.05)
                    if overflow.is_set():
                        outcome = "output_limit"
                    elif ready.is_set() and profile.kind == "unittest":
                        # Bounded file read while container tmpfs is alive. Never parse logs as verdicts.
                        raw = self._control(
                            "exec",
                            run_id,
                            "python",
                            "-I",
                            "-c",
                            "import sys;sys.stdout.buffer.write(open('/tmp/ntg-result.json','rb').read(4097))",
                        ).stdout
                        result = json.loads(raw) if len(raw) <= 4096 else {}
                        tests_run, exit_code = result["tests_run"], result["exit_code"]
                        if (
                            type(tests_run) is not int
                            or tests_run < 0
                            or type(exit_code) is not int
                        ):
                            raise ValueError()
                        outcome = (
                            "no_tests"
                            if not tests_run
                            else "passed"
                            if exit_code == 0
                            else "failed"
                        )
                    elif proc.poll() is None:
                        outcome = "timeout"
                    else:
                        state = json.loads(
                            self._control("inspect", "--format", "{{json .State}}", run_id).stdout
                        )
                        if state.get("Running") or not state.get("StartedAt", "").startswith("20"):
                            raise ValueError()
                        exit_code = state["ExitCode"]
                        outcome = (
                            "resource_limit"
                            if state.get("OOMKilled")
                            else (
                                "passed"
                                if exit_code == 0 and profile.kind == "script"
                                else "failed"
                            )
                        )
            except (
                OSError,
                ValueError,
                KeyError,
                ToolError,
                RoutingError,
                subprocess.SubprocessError,
            ):
                outcome, error = "infrastructure_error", "sandbox_execution_failed"
            finally:
                if attempted:
                    try:
                        self._control("rm", "--force", run_id, check=False)
                        remaining = self._control(
                            "ps", "--all", "--quiet", "--filter", f"name=^/{run_id}$"
                        )
                        cleanup = not remaining.stdout.strip()
                    except (OSError, RoutingError, subprocess.SubprocessError):
                        cleanup = False
                if proc:
                    try:
                        proc.wait(timeout=self.policy.control_timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=self.policy.control_timeout)
                    for thread in readers:
                        thread.join(timeout=self.policy.control_timeout)
                    if any(thread.is_alive() for thread in readers):
                        cleanup = False
                    if not any(thread.is_alive() for thread in readers):
                        proc.stdout.close()
                        proc.stderr.close()
                if not cleanup:
                    outcome, error = "infrastructure_error", "cleanup_unconfirmed"
                elif overflow.is_set() and outcome != "infrastructure_error":
                    outcome = "output_limit"
        return ExecutionReport(
            run_id=run_id,
            snapshot_id=snapshot_id(manifest),
            kind=profile.kind,
            outcome=outcome,
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - started) * 1000),
            stdout=safe_text(bytes(buffers[0])).replace(marker.decode(), ""),
            stderr=safe_text(bytes(buffers[1])),
            stdout_truncated=truncated[0],
            stderr_truncated=truncated[1],
            tests_run=tests_run,
            cleanup_confirmed=cleanup,
            error_code=error,
        )
