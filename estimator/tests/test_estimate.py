import json

import pytest
from fastapi.testclient import TestClient

from app.services.llm_service import build_system_prompt
from tests.conftest import FAKE_CHUNKS, FakeWrapper, parse_sse

TRANSCRIPTION = "El cliente necesita una landing page con formulario de contacto y blog."


def test_estimate_returns_estimation(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200
    body = response.json()
    assert body["estimation"].startswith("## Estimación")
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini-2024-07-18"
    assert body["usage"]["total_tokens"] == 2100
    assert body["cached"] is False
    assert "created_at" in body
    # El servicio envía el system prompt CAG y la transcripción como único mensaje
    assert fake_wrapper.calls == [
        (build_system_prompt(), [{"role": "user", "content": TRANSCRIPTION}])
    ]


@pytest.mark.parametrize("path", ["/api/v1/estimate", "/api/v1/estimate/stream"])
@pytest.mark.parametrize("payload", [{}, {"transcription": ""}, {"transcription": "corto"}])
def test_invalid_body_returns_422(
    client: TestClient, fake_wrapper: FakeWrapper, path: str, payload: dict
) -> None:
    assert client.post(path, json=payload).status_code == 422
    assert fake_wrapper.calls == []


def test_llm_failure_returns_502(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    fake_wrapper.error = "Error del proveedor openai: 401 invalid api key"
    response = client.post("/api/v1/estimate", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 502
    assert "401" in response.json()["detail"]


def test_stream_emits_tokens_then_done(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    response = client.post("/api/v1/estimate/stream", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    tokens = [json.loads(data) for event, data in events if event == "token"]
    assert tokens == list(FAKE_CHUNKS)  # los saltos de línea viajan dentro del JSON
    event, data = events[-1]
    assert event == "done"
    done = json.loads(data)
    assert done["usage"]["total_tokens"] == 2100
    assert done["cached"] is False
    assert "estimation" not in done  # el texto ya llegó en los tokens
    assert fake_wrapper.calls[0][1] == [{"role": "user", "content": TRANSCRIPTION}]


def test_stream_failure_emits_error_event(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    fake_wrapper.error = "Error del proveedor openai: sin crédito"
    response = client.post("/api/v1/estimate/stream", json={"transcription": TRANSCRIPTION})
    assert response.status_code == 200  # la respuesta ya había empezado

    events = parse_sse(response.text)
    assert [event for event, _ in events] == ["token", "error"]
    assert "sin crédito" in json.loads(events[-1][1])["detail"]


def test_responses_carry_request_id(client: TestClient) -> None:
    assert client.get("/health").headers["X-Request-ID"]
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
