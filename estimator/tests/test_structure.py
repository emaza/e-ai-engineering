"""Valida que el proyecto mantiene la estructura pedida en el enunciado."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    "app/__init__.py",
    "app/main.py",
    "app/config.py",
    "app/routers/__init__.py",
    "app/routers/estimations.py",
    "app/services/__init__.py",
    "app/services/llm_service.py",
    "app/prompts/__init__.py",
    "app/prompts/loader.py",
    "app/prompts/estimation/v1/system.j2",
    "app/prompts/estimation/v1/user.j2",
    "app/prompts/estimation/v1/examples.j2",
    "app/prompts/estimation/v2/system.j2",
    "app/prompts/estimation/v2/user.j2",
    "app/prompts/estimation/v2/examples.j2",
    "app/services/llm_wrapper.py",
    "app/services/cache.py",
    "app/logging_config.py",
    "streamlit_app.py",
    "estimator_client.py",
    "tests/prompts/test_estimation_v1.py",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
]


@pytest.mark.parametrize("relative_path", REQUIRED_PATHS)
def test_required_path_exists(relative_path: str) -> None:
    assert (ROOT / relative_path).is_file(), f"Falta {relative_path}"


def test_env_is_gitignored() -> None:
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored


def test_env_example_documents_all_variables() -> None:
    content = (ROOT / ".env.example").read_text()
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "APP_ENV",
        "LOG_LEVEL",
        "CACHE_ENABLED",
        "CACHE_TTL_SECONDS",
        "CACHE_MAX_ENTRIES",
    ):
        assert f"{var}=" in content, f"{var} no está documentada en .env.example"


def test_env_example_has_no_real_keys() -> None:
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.endswith("_API_KEY=") or line.startswith("#"):
            continue
        assert "_API_KEY=" not in line, f"Posible key real en .env.example: {line.split('=')[0]}"


def test_dockerignore_excludes_secrets() -> None:
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in ignored
