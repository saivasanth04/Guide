"""main.py is one routed Google-style generate_content call."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from ntg_llm import LLMError


def test_one_generation_call(monkeypatch, capsys):
    generate = Mock(return_value=SimpleNamespace(text="Generative AI creates new content."))
    monkeypatch.setattr(
        main,
        "LLMClient",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )
    main.main()
    generate.assert_called_once_with(
        model="router", contents="Explain generative AI in one sentence."
    )
    assert capsys.readouterr().out == "Generative AI creates new content.\n"


def test_provider_error_remains_structured(monkeypatch, capsys):
    generate = Mock(side_effect=LLMError(429, "Provider unavailable", retry_after=2))
    monkeypatch.setattr(
        main,
        "LLMClient",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )
    with pytest.raises(SystemExit) as error:
        main.main()
    assert error.value.code == 1
    generate.assert_called_once()
    assert '"status": 429' in capsys.readouterr().err
