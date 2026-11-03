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
    "app/context/__init__.py",
    "app/context/examples.py",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
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
    ):
        assert f"{var}=" in content, f"{var} no está documentada en .env.example"


def test_env_example_has_no_real_keys() -> None:
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.endswith("_API_KEY=") or line.startswith("#"):
            continue
        assert "_API_KEY=" not in line, f"Posible key real en .env.example: {line.split('=')[0]}"
