"""Tests de la interfaz Streamlit con AppTest: el LLM se sustituye por un stream falso."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import config
from app.services import llm_service
from tests.conftest import make_settings

APP_PATH = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")

RESULT = {
    "estimation": "## Estimación: Test",
    "model": "gpt-4o-mini-2024-07-18",
    "provider": "openai",
    "usage": {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500},
    "finish_reason": "stop",
    "latency_ms": 2500,
    "estimated_cost_usd": 0.00045,
}


class FakeStream:
    """Sustituye a EstimationStream: guarda el historial recibido y da trozos fijos."""

    calls: list[list[dict]] = []
    fail = False

    def __init__(self, messages, settings) -> None:
        FakeStream.calls.append(messages)
        self.result = None

    async def __aiter__(self):
        if FakeStream.fail:
            raise llm_service.LLMServiceError("Error del proveedor openai: sin crédito")
        for chunk in ("## Estimación", ": ", "Test"):
            yield chunk
        self.result = RESULT


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    FakeStream.calls = []
    FakeStream.fail = False
    monkeypatch.setattr(config, "get_settings", lambda: make_settings())
    monkeypatch.setattr(llm_service, "EstimationStream", FakeStream)
    return AppTest.from_file(APP_PATH, default_timeout=10).run()


def test_initial_render(app: AppTest) -> None:
    assert not app.exception
    assert len(app.chat_input) == 1
    assert len(app.chat_message) == 0
    sidebar_text = " ".join(e.label for e in app.sidebar.expander)
    assert "Ver system prompt activo" in sidebar_text
    assert sidebar_text.count("Ejemplo ") == 3
    assert app.sidebar.code[0].value == llm_service.build_system_prompt()


def test_chat_streams_estimation_and_keeps_history(app: AppTest) -> None:
    app.chat_input[0].set_value("Transcripción de la reunión").run()
    assert not app.exception
    assert [m.name for m in app.chat_message] == ["user", "assistant"]
    assert "Estimación: Test" in app.chat_message[1].markdown[0].value

    metrics = {m.label: m.value for m in app.sidebar.metric}
    assert any("gpt-4o-mini-2024-07-18" in m.value for m in app.sidebar.markdown)
    assert metrics["Tokens entrada"] == "1.000"
    assert metrics["Tokens salida"] == "500"
    assert metrics["Tiempo"] == "2.5 s"

    # El segundo mensaje envía el historial completo al LLM
    app.chat_input[0].set_value("quita el diseñador").run()
    assert len(app.chat_message) == 4
    assert [m["role"] for m in FakeStream.calls[-1]] == ["user", "assistant", "user"]
    assert FakeStream.calls[-1][1]["content"] == "## Estimación: Test"


def test_provider_error_is_shown_and_not_kept(app: AppTest) -> None:
    FakeStream.fail = True
    app.chat_input[0].set_value("Transcripción de la reunión").run()
    assert not app.exception
    assert "sin crédito" in app.error[0].value
    assert app.session_state.messages == []


def test_new_conversation_clears_history(app: AppTest) -> None:
    app.chat_input[0].set_value("Transcripción de la reunión").run()
    app.sidebar.button[0].click().run()
    assert len(app.chat_message) == 0
    assert app.session_state.last_result is None
