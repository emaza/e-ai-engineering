import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_defaults() -> None:
    settings = make_settings()
    assert settings.LLM_PROVIDER == "openai"
    assert settings.model == "gpt-4o-mini"
    assert settings.APP_ENV == "development"
    assert settings.LOG_LEVEL == "DEBUG"


def test_default_model_follows_provider() -> None:
    assert make_settings(LLM_PROVIDER="anthropic").model == "claude-haiku-4-5"


def test_explicit_model_wins() -> None:
    assert make_settings(LLM_MODEL="gpt-4.1-mini").model == "gpt-4.1-mini"


@pytest.mark.parametrize(
    ("provider", "missing_key"),
    [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")],
)
def test_requires_key_of_selected_provider(provider: str, missing_key: str) -> None:
    with pytest.raises(ValidationError, match=missing_key):
        make_settings(LLM_PROVIDER=provider, **{missing_key: None})


def test_invalid_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(LLM_PROVIDER="gemini")
