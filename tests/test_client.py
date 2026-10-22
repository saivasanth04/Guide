import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import json
import pytest
from routing import RotationStore, CompletionService, RoutingError

from tests.helpers import FakeRouter, deployments


def test_chat_client_preserves_roles_and_returns_openai_style_content(tmp_path, monkeypatch):
    import chat_client

    class RecordingRouter(FakeRouter):
        def completion(self, **kwargs):
            self.messages = kwargs["messages"]
            return super().completion(**kwargs)

    service = CompletionService(
        deployments(), RotationStore(tmp_path / "quota.db"), RecordingRouter
    )
    monkeypatch.setattr(chat_client, "_service", service)
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "Write Fibonacci code."},
    ]
    response = chat_client.ChatClient().chat.completions.create(model="router", messages=messages)
    assert response.choices[0].message.content == "actual answer"
    assert service.router.messages == messages
    assert response.model == service.deployments[0].model
    assert response.diagnostics["key_label"] == service.deployments[0].key_label


def test_chat_client_invalid_messages_make_no_attempt(tmp_path, monkeypatch):
    import chat_client

    service = CompletionService(deployments(), RotationStore(tmp_path / "quota.db"), FakeRouter)
    monkeypatch.setattr(chat_client, "_service", service)
    client = chat_client.ChatClient()
    for messages in (
        [],
        [{"role": "unknown", "content": "hi"}],
        [{"role": "user", "content": ["hi"]}],
    ):
        with pytest.raises(RoutingError):
            client.chat.completions.create(messages=messages)
    with pytest.raises(RoutingError):
        client.chat.completions.create(
            model="gemini-3.8-flash", messages=[{"role": "user", "content": "hi"}]
        )
    assert not service.router.calls


def test_legacy_structured_completion_interface(tmp_path, monkeypatch):
    import chat_client
    from llm import structured_completion
    from ntg_llm import structured_completion as packaged_completion

    service = CompletionService(deployments(), RotationStore(tmp_path / "quota.db"), FakeRouter)
    monkeypatch.setattr(chat_client, "_service", service)
    assert structured_completion is packaged_completion
    output = structured_completion("hi")
    assert output["answer"] == "actual answer"
    assert output["diagnostics"]["failed_attempts"] == []
    assert output["diagnostics"]["attempt_count"] == 1


def test_success_includes_failed_accounts_models_and_retries(tmp_path, monkeypatch):
    import chat_client

    class Unavailable(Exception):
        status_code = 503

    class RecoveringRouter(FakeRouter):
        def completion(self, **kwargs):
            self.error = Unavailable("mock-secret") if len(self.calls) < 3 else None
            return super().completion(**kwargs)

    available = deployments()
    ds = [available[0], available[3]]
    ds[0].account_label = "first-account"
    ds[1].account_label = "second-account"
    service = CompletionService(
        ds, RotationStore(tmp_path / "state.db"), RecoveringRouter, sleeper=lambda _: None
    )
    monkeypatch.setattr(chat_client, "_service", service)
    response = chat_client.LLMClient().chat.completions.create(
        messages=[{"role": "user", "content": "hi"}]
    )
    failures = response.diagnostics["failed_attempts"]
    assert len(failures) == 3
    assert [f["retry_number"] for f in failures] == [0, 1, 2]
    assert all(
        f["account_label"] == "first-account" and f["selected_model"] == ds[0].model
        for f in failures
    )
    assert response.diagnostics["account_label"] == "second-account"
    assert response.diagnostics["attempt_count"] == 4
    assert [a["status"] for a in response.diagnostics["attempts"]] == [503, 503, 503, 200]
    assert "mock-secret" not in json.dumps(response.diagnostics)
