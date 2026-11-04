"""Tests de la UI con AppTest, de punta a punta: UI → SSE → FastAPI (con wrapper falso).

`httpx.stream` se redirige al TestClient de la app real, así se prueba el contrato SSE
completo (eventos token / done / error) sin red ni proveedor LLM.
"""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from tests.conftest import FakeWrapper

APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"
API_URL = "http://localhost:8000"
TRANSCRIPTION = "El cliente necesita una landing page con formulario de contacto y blog."


@pytest.fixture
def ui(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    def stream_via_test_client(method, url, *, json, timeout):
        return client.stream(method, url.removeprefix(API_URL), json=json)

    monkeypatch.delenv("ESTIMATOR_API_URL", raising=False)
    monkeypatch.setattr(httpx, "stream", stream_via_test_client)
    return AppTest.from_file(str(APP_PATH), default_timeout=10).run()


def test_initial_render(ui: AppTest) -> None:
    assert not ui.exception
    assert len(ui.chat_input) == 1
    assert len(ui.chat_message) == 0
    assert API_URL in ui.sidebar.caption[0].value


def test_streams_estimation_from_api(ui: AppTest, fake_wrapper: FakeWrapper) -> None:
    ui.chat_input[0].set_value(TRANSCRIPTION).run()
    assert not ui.exception
    assert [m.name for m in ui.chat_message] == ["user", "assistant"]
    assert "Estimación: Landing page" in ui.chat_message[1].markdown[0].value

    metrics = {m.label: m.value for m in ui.sidebar.metric}
    assert metrics["Tokens entrada"] == "1.500"
    assert metrics["Tokens salida"] == "600"
    assert metrics["Tiempo"] == "1.2 s"
    assert any("gpt-4o-mini-2024-07-18" in m.value for m in ui.sidebar.markdown)

    # Cada mensaje es una estimación nueva: a la API solo llega el último
    ui.chat_input[0].set_value("Otra reunión: app móvil de reservas para un gimnasio.").run()
    assert len(ui.chat_message) == 4
    assert len(fake_wrapper.calls[-1][1]) == 1


def test_validation_error_is_shown(ui: AppTest) -> None:
    ui.chat_input[0].set_value("corto").run()
    assert "422" in ui.error[0].value
    assert ui.session_state.messages == []


def test_provider_error_event_is_shown(ui: AppTest, fake_wrapper: FakeWrapper) -> None:
    fake_wrapper.error = "Error del proveedor openai: sin crédito"
    ui.chat_input[0].set_value(TRANSCRIPTION).run()
    assert "sin crédito" in ui.error[0].value
    assert ui.session_state.messages == []
    assert ui.session_state.last_result is None


def test_api_down_is_shown(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "stream", refuse)
    ui.chat_input[0].set_value(TRANSCRIPTION).run()
    assert "No se puede conectar con la API" in ui.error[0].value


def test_new_conversation_clears_history(ui: AppTest) -> None:
    ui.chat_input[0].set_value(TRANSCRIPTION).run()
    ui.sidebar.button[0].click().run()
    assert len(ui.chat_message) == 0
    assert ui.session_state.last_result is None


def test_ui_does_not_touch_backend_code_or_keys() -> None:
    source = APP_PATH.read_text()
    assert "from app" not in source and "import app" not in source
    assert "API_KEY" not in source
    assert "dotenv" not in source
