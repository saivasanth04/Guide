"""Prevent accidental provider calls across the entire test suite."""

import pytest


@pytest.fixture(autouse=True)
def block_live_http(monkeypatch):
    import httpx

    def blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is prohibited in mocked tests")

    async def async_blocked(*args, **kwargs):
        raise AssertionError("Live HTTP is prohibited in mocked tests")

    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_blocked)
