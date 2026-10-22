import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ntg_llm import LLMError
from ntg_llm.sandbox import DockerSandboxRunner, safe_text
from ntg_llm.testing_models import ExecutionPolicy, TestProfile
from ntg_llm.tools.filesystem import FilesystemTools
from ntg_llm.tools.models import ToolError


IMAGE = "sha256:" + "1" * 64


@pytest.mark.parametrize(
    "kwargs", [{"timeout_seconds": True}, {"output_bytes": 0}, {"image_id": "python:latest"}]
)
def test_policy_rejects_unsafe_or_coerced_values(kwargs):
    with pytest.raises(ValidationError):
        ExecutionPolicy(**{"image_id": IMAGE, **kwargs})


def test_snapshot_excludes_secrets_and_checks_resources(tmp_path):
    root, stage = tmp_path / "project", tmp_path / "stage"
    root.mkdir()
    (root / "app.py").write_text("print(1)")
    (root / ".env").write_text("SECRET")
    (root / "data.txt").write_text("data")
    manifest = FilesystemTools(root).snapshot(["data.txt"], stage)
    assert set(manifest) == {"app.py", "data.txt"}
    assert not (stage / ".env").exists()
    with pytest.raises(ToolError):
        FilesystemTools(root).snapshot(["../escape"], stage)
    with pytest.raises(ToolError):
        FilesystemTools(root).snapshot([], stage, max_total_bytes=1)


def test_snapshot_rejects_hardlinks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("pass")
    (root / "b.py").hardlink_to(root / "a.py")
    with pytest.raises(ToolError):
        FilesystemTools(root).snapshot([], tmp_path / "staging")


def test_preflight_checks_local_engine_and_immutable_image(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    runner = DockerSandboxRunner(ExecutionPolicy(image_id=IMAGE))
    results = [
        json.dumps([{"Endpoints": {"docker": {"Host": "npipe://local"}}}]).encode(),
        b"linux\n",
        json.dumps([{"Id": IMAGE, "Os": "linux"}]).encode(),
    ]
    monkeypatch.setattr(runner, "_control", lambda *a, **kw: SimpleNamespace(stdout=results.pop(0)))
    runner.preflight(tmp_path, TestProfile(kind="script", entrypoint="future.py"))
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote")
    with pytest.raises(LLMError):
        runner.preflight(tmp_path, TestProfile(kind="script", entrypoint="future.py"))


def test_missing_generated_entrypoint_is_repairable_without_container(tmp_path, monkeypatch):
    runner = DockerSandboxRunner(ExecutionPolicy(image_id=IMAGE))
    monkeypatch.setattr(runner, "_control", lambda *a, **k: pytest.fail("No container needed"))
    report = runner.run(tmp_path, TestProfile(kind="script", entrypoint="missing.py"))
    assert report.outcome == "failed" and report.error_code == "missing_entrypoint"


def test_failed_container_create_attempts_owned_cleanup(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("pass")
    runner = DockerSandboxRunner(ExecutionPolicy(image_id=IMAGE))
    calls = []

    def control(*args, **kwargs):
        calls.append(args)
        if args[0] == "create":
            raise LLMError(503, "Create failed")
        return SimpleNamespace(stdout=b"")

    monkeypatch.setattr(runner, "_control", control)
    report = runner.run(tmp_path, TestProfile(kind="script", entrypoint="app.py"))
    assert report.outcome == "infrastructure_error" and report.cleanup_confirmed
    assert [c[0] for c in calls] == ["create", "rm", "ps"]
    flags = calls[0]
    for required in (
        "--read-only",
        "--network",
        "--cap-drop",
        "--memory",
        "--pids-limit",
        "--user",
    ):
        assert required in flags


def test_terminal_controls_removed():
    assert safe_text(b"\x1b[31merror\x00\n") == "error\n"


@pytest.mark.parametrize("cleanup_failure", [False, True])
@pytest.mark.parametrize("entrypoint", ["app.py", "./app.py", ".\\app.py"])
def test_full_process_lifecycle_and_cleanup_uncertainty(
    tmp_path, monkeypatch, cleanup_failure, entrypoint
):
    import io

    (tmp_path / "app.py").write_text("print('ran')")
    runner = DockerSandboxRunner(ExecutionPolicy(image_id=IMAGE))
    calls = []

    class Process:
        stdout = io.BytesIO(b"ran\n")
        stderr = io.BytesIO(b"")

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    def control(*args, **kwargs):
        calls.append(args[0])
        if args[0] == "inspect":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "Running": False,
                        "StartedAt": "2026-10-22T00:00:00Z",
                        "ExitCode": 0,
                        "OOMKilled": False,
                    }
                ).encode()
            )
        if args[0] == "ps" and cleanup_failure:
            raise LLMError(503, "Cleanup status unavailable")
        return SimpleNamespace(stdout=b"")

    monkeypatch.setattr(runner, "_control", control)
    monkeypatch.setattr("ntg_llm.sandbox.subprocess.Popen", lambda *a, **k: Process())
    report = runner.run(tmp_path, TestProfile(kind="script", entrypoint=entrypoint))
    assert calls == ["create", "inspect", "rm", "ps"]
    assert report.stdout == "ran\n"
    assert report.outcome == ("infrastructure_error" if cleanup_failure else "passed")
    assert report.cleanup_confirmed is not cleanup_failure
    assert report.error_code == ("cleanup_unconfirmed" if cleanup_failure else None)
