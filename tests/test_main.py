"""The application makes exactly one completion call per valid prompt."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from ntg_llm import LLMError


@pytest.mark.parametrize("interactive", [False, True])
def test_one_prompt_one_completion(monkeypatch, capsys, interactive):
    create = Mock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello there"))]
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main, "LLMClient", lambda: client)
    monkeypatch.setattr("sys.argv", ["main.py"] if interactive else ["main.py", "Hello"])
    monkeypatch.setattr("builtins.input", lambda: "Hello")
    main.main()
    create.assert_called_once_with(messages=[{"role": "user", "content": "Hello"}])
    assert capsys.readouterr().out == "Hello there\n"


@pytest.mark.parametrize("prompt", ["", "   "])
def test_blank_prompt_does_not_call_model(monkeypatch, prompt):
    client = Mock()
    monkeypatch.setattr(main, "LLMClient", client)
    monkeypatch.setattr("sys.argv", ["main.py", prompt])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 2
    client.assert_not_called()


def test_help_does_not_initialize_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(main, "LLMClient", client)
    monkeypatch.setattr("sys.argv", ["main.py", "--help"])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 0
    client.assert_not_called()


def test_provider_failure_remains_structured_without_an_extra_call(monkeypatch, capsys):
    create = Mock(side_effect=LLMError(429, "Provider unavailable", retry_after=2))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(main, "LLMClient", lambda: client)
    monkeypatch.setattr("sys.argv", ["main.py", "Hello"])
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 1
    create.assert_called_once()
    assert '"status": 429' in capsys.readouterr().err
