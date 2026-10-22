"""Prevent accidental provider calls across the entire test suite."""

import pytest


def pytest_addoption(parser):
    parser.addoption("--require-sandbox", action="store_true", default=False)
    parser.addoption("--sandbox-image-id", default=None)


@pytest.fixture
def sandbox_policy(request, tmp_path):
    from ntg_llm.testing_models import ExecutionPolicy, TestProfile
    from ntg_llm.sandbox import DockerSandboxRunner

    try:
        policy = ExecutionPolicy(image_id=request.config.getoption("--sandbox-image-id"))
        DockerSandboxRunner(policy).preflight(
            tmp_path, TestProfile(kind="script", entrypoint="main.py")
        )
    except Exception:
        if request.config.getoption("--require-sandbox"):
            pytest.fail("Required Linux Docker sandbox/image is unavailable")
        pytest.skip("Pass --sandbox-image-id with a reachable Linux Docker image")
    return policy


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch):
    import httpx

    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is prohibited in mocked tests")

    async def async_blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is prohibited in mocked tests")

    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_blocked)
