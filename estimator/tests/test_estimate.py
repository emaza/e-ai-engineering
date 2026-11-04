import json

import pytest
from fastapi.testclient import TestClient

from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest
from tests.conftest import FAKE_CHUNKS, FakeWrapper, parse_sse

BODY = {
    "description": "Landing page con formulario de contacto, integración con HubSpot y blog.",
    "project_type": "web_saas",
    "detail_level": "medium",
    "output_format": "phases_table",
}


def test_estimate_returns_text_version_and_metadata(
    client: TestClient, fake_wrapper: FakeWrapper
) -> None:
    response = client.post("/api/v1/estimate", json=BODY)
    assert response.status_code == 200
    body = response.json()
    assert body["text"].startswith("## Estimación")
    assert body["prompt_version"] == "v1"
    assert body["metadata"]["model"] == "gpt-4o-mini-2024-07-18"
    assert body["metadata"]["usage"]["total_tokens"] == 2100
    assert body["metadata"]["cached"] is False
    assert "estimation" not in body["metadata"]


def test_system_and_user_travel_as_separate_messages(
    client: TestClient, fake_wrapper: FakeWrapper
) -> None:
    client.post("/api/v1/estimate", json=BODY)
    system, user = render_estimation_prompt(EstimationRequest(**BODY), "v1")
    assert fake_wrapper.calls == [(system, [{"role": "user", "content": user}])]


def test_prompt_version_query_param(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    response = client.post("/api/v1/estimate?prompt_version=v2", json=BODY)
    assert response.json()["prompt_version"] == "v2"
    system_v2, _ = render_estimation_prompt(EstimationRequest(**BODY), "v2")
    assert fake_wrapper.calls[0][0] == system_v2


@pytest.mark.parametrize("path", ["/api/v1/estimate", "/api/v1/estimate/stream"])
@pytest.mark.parametrize(
    ("query", "payload"),
    [
        ("", {}),
        ("", BODY | {"description": "corto"}),
        ("", BODY | {"description": "x" * 2001}),
        ("", BODY | {"project_type": "MOBILE_APP"}),  # hay que enviar el valor, no el nombre
        ("", BODY | {"output_format": "pdf"}),
        ("", {k: v for k, v in BODY.items() if k != "detail_level"}),
        ("", BODY | {"reference_projects": [{"name": "X", "description": "Y"}]}),
        ("?prompt_version=v9", BODY),
    ],
)
def test_invalid_requests_return_422(
    client: TestClient, fake_wrapper: FakeWrapper, path: str, query: str, payload: dict
) -> None:
    assert client.post(path + query, json=payload).status_code == 422
    assert fake_wrapper.calls == []


def test_reference_projects_reach_the_user_prompt(
    client: TestClient, fake_wrapper: FakeWrapper
) -> None:
    body = BODY | {
        "reference_projects": [
            {"name": "Web despacho", "description": "Web con blog", "actual_hours": 110}
        ]
    }
    assert client.post("/api/v1/estimate", json=body).status_code == 200
    user = fake_wrapper.calls[0][1][0]["content"]
    assert "Web despacho (110 horas reales)" in user


def test_llm_failure_returns_502(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    fake_wrapper.error = "Error del proveedor openai: 401 invalid api key"
    response = client.post("/api/v1/estimate", json=BODY)
    assert response.status_code == 502
    assert "401" in response.json()["detail"]


def test_stream_emits_tokens_then_done(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    response = client.post("/api/v1/estimate/stream?prompt_version=v2", json=BODY)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    tokens = [json.loads(data) for event, data in events if event == "token"]
    assert tokens == list(FAKE_CHUNKS)  # los saltos de línea viajan dentro del JSON
    event, data = events[-1]
    assert event == "done"
    done = json.loads(data)
    assert done["prompt_version"] == "v2"
    assert done["metadata"]["usage"]["total_tokens"] == 2100
    assert "text" not in done  # el texto ya llegó en los tokens


def test_stream_failure_emits_error_event(client: TestClient, fake_wrapper: FakeWrapper) -> None:
    fake_wrapper.error = "Error del proveedor openai: sin crédito"
    response = client.post("/api/v1/estimate/stream", json=BODY)
    assert response.status_code == 200  # la respuesta ya había empezado

    events = parse_sse(response.text)
    assert [event for event, _ in events] == ["token", "error"]
    assert "sin crédito" in json.loads(events[-1][1])["detail"]


def test_responses_carry_request_id(client: TestClient) -> None:
    assert client.get("/health").headers["X-Request-ID"]
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
