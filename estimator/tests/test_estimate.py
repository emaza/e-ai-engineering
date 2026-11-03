import pytest
from fastapi.testclient import TestClient

from app.routers import estimations
from app.services.llm_service import LLMServiceError

TRANSCRIPTION = "El cliente necesita una landing page con formulario de contacto y blog."

FAKE_RESULT = {
    "estimation": "## Estimación: Landing page\n\n| Tarea | Perfil | Horas | Coste |",
    "model": "gpt-4o-mini-2024-07-18",
    "provider": "openai",
    "usage": {"input_tokens": 1500, "output_tokens": 600, "total_tokens": 2100},
    "finish_reason": "stop",
    "latency_ms": 1234,
    "estimated_cost_usd": 0.000585,
}


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake_generate(transcription, settings):
        calls.append(transcription)
        return dict(FAKE_RESULT)

    monkeypatch.setattr(estimations, "generate_estimation", fake_generate)
    return calls


def test_estimate_returns_estimation(client: TestClient, fake_llm: list[str]) -> None:
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200
    body = response.json()
    assert body["estimation"].startswith("## Estimación")
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini-2024-07-18"
    assert body["usage"]["total_tokens"] == 2100
    assert "created_at" in body
    assert fake_llm == [TRANSCRIPTION]


@pytest.mark.parametrize("payload", [{}, {"transcription": ""}, {"transcription": "corto"}])
def test_invalid_body_returns_422(client: TestClient, fake_llm: list[str], payload: dict) -> None:
    assert client.post("/api/v1/estimate", json=payload).status_code == 422
    assert fake_llm == []


def test_llm_failure_returns_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_generate(transcription, settings):
        raise LLMServiceError("Error del proveedor openai: 401 invalid api key")

    monkeypatch.setattr(estimations, "generate_estimation", failing_generate)
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 502
    assert "401" in response.json()["detail"]
