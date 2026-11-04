from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.services.llm_wrapper import LLMError, get_llm_wrapper


def make_settings(**overrides) -> Settings:
    """Settings de test: ignoran el .env local para no depender de keys reales."""
    values = {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"} | overrides
    return Settings(_env_file=None, **values)


FAKE_CHUNKS = ("## Estimación: Landing page\n\n", "| Tarea | Perfil | Horas | Coste |")

FAKE_RESULT: dict[str, Any] = {
    "estimation": "".join(FAKE_CHUNKS),
    "model": "gpt-4o-mini-2024-07-18",
    "provider": "openai",
    "usage": {"input_tokens": 1500, "output_tokens": 600, "total_tokens": 2100},
    "finish_reason": "stop",
    "latency_ms": 1234,
    "estimated_cost_usd": 0.000585,
    "cached": False,
}


class FakeWrapper:
    """Sustituye a LLMWrapper en los tests de endpoints: no llama a ningún proveedor.

    Con `error` definido, `complete()` falla y `stream()` falla tras el primer trozo.
    """

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def complete(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append((system, messages))
        if self.error:
            raise LLMError(self.error)
        return dict(FAKE_RESULT)

    def stream(self, system: str, messages: list[dict[str, str]]) -> "FakeStream":
        self.calls.append((system, messages))
        return FakeStream(self.error)


class FakeStream:
    def __init__(self, error: str | None) -> None:
        self.error = error
        self.result: dict[str, Any] | None = None

    async def __aiter__(self):
        for i, chunk in enumerate(FAKE_CHUNKS):
            if self.error and i == 1:
                raise LLMError(self.error)
            yield chunk
        self.result = dict(FAKE_RESULT)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_wrapper() -> FakeWrapper:
    return FakeWrapper()


@pytest.fixture
def client(settings: Settings, fake_wrapper: FakeWrapper):
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm_wrapper] = lambda: fake_wrapper
    yield TestClient(app)
    app.dependency_overrides.clear()


def parse_sse(body: str) -> list[tuple[str, str]]:
    """Separa un cuerpo SSE en (event, data). Ignora comentarios (keep-alive)."""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "data" in fields:
            events.append((fields.get("event", "message"), fields["data"]))
    return events
