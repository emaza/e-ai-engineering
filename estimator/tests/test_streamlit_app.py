"""Tests de la UI con AppTest, de punta a punta: UI → SSE → FastAPI (con wrapper falso).

`httpx.stream` se redirige al TestClient de la app real, así se prueba el contrato
completo: el formulario produce un EstimationRequest válido, la API renderiza los
templates y los eventos token / done / error vuelven a la UI. Sin red ni proveedor LLM.
"""

from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

import estimator_client
from app.prompts.loader import render_estimation_prompt
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType, PromptVersion
from tests.conftest import FakeWrapper

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "streamlit_app.py"
API_URL = "http://localhost:8000"
DESCRIPTION = "Landing page con formulario de contacto, integración con HubSpot y blog."


@pytest.fixture
def ui(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    def stream_via_test_client(method, url, *, json, params, timeout):
        return client.stream(method, url.removeprefix(API_URL), json=json, params=params)

    monkeypatch.delenv("ESTIMATOR_API_URL", raising=False)
    monkeypatch.setattr(httpx, "stream", stream_via_test_client)
    return AppTest.from_file(str(APP_PATH), default_timeout=10).run()


def fill_form(ui: AppTest, description: str = DESCRIPTION, **choices: str) -> AppTest:
    ui.text_area[0].input(description)
    ui.selectbox[0].select(choices.get("project_type", "web_saas"))
    ui.selectbox[1].select(choices.get("output_format", "phases_table"))
    ui.radio[0].set_value(choices.get("detail_level", "medium"))
    ui.selectbox[2].select(choices.get("prompt_version", "v1"))
    return ui.main.button[0].click().run()


def test_initial_render_is_a_form(ui: AppTest) -> None:
    assert not ui.exception
    assert len(ui.text_area) == 1
    assert len(ui.chat_input) == 0  # ya no es un chat
    assert [s.label for s in ui.selectbox] == [
        "Tipo de proyecto",
        "Formato de salida",
        "Versión del prompt",
    ]
    assert ui.radio[0].value == "medium"


def test_form_options_match_api_enums() -> None:
    # La UI no importa app/: este test vigila que sus opciones no se desincronicen
    assert set(estimator_client.PROJECT_TYPES) == {e.value for e in ProjectType}
    assert set(estimator_client.DETAIL_LEVELS) == {e.value for e in DetailLevel}
    assert set(estimator_client.OUTPUT_FORMATS) == {e.value for e in OutputFormat}
    assert set(estimator_client.PROMPT_VERSIONS) == set(PromptVersion.__args__)


def test_form_sends_typed_request_and_streams_result(
    ui: AppTest, fake_wrapper: FakeWrapper
) -> None:
    fill_form(
        ui,
        project_type="data_pipeline",
        output_format="narrative",
        detail_level="detailed",
        prompt_version="v2",
    )
    assert not ui.exception
    assert not ui.error

    # La API recibió exactamente el prompt que corresponde a esos parámetros
    expected = EstimationRequest(
        description=DESCRIPTION,
        project_type="data_pipeline",
        detail_level="detailed",
        output_format="narrative",
    )
    system, user = render_estimation_prompt(expected, "v2")
    assert fake_wrapper.calls == [(system, [{"role": "user", "content": user}])]

    assert any("Estimación: Landing page" in m.value for m in ui.main.markdown)
    captions = [c.value for c in ui.main.caption]
    assert "Pipeline de datos · Detallado · Narrativo · prompt v2" in captions
    metrics = {m.label: m.value for m in ui.sidebar.metric}
    assert metrics["Tokens entrada"] == "1.500"
    assert any("`v2`" in m.value for m in ui.sidebar.markdown)


def test_history_keeps_previous_estimations(ui: AppTest) -> None:
    fill_form(ui, output_format="line_items")
    fill_form(ui, output_format="narrative")
    assert len(ui.session_state.history) == 2
    labels = [e.label for e in ui.main.expander]
    assert labels == [
        "Proyectos de referencia (opcional)",
        "SaaS web · Medio · Partidas · prompt v1",
    ]


def test_short_description_is_not_sent(ui: AppTest, fake_wrapper: FakeWrapper) -> None:
    fill_form(ui, description="corto")
    assert "al menos 20 caracteres" in ui.warning[0].value
    assert fake_wrapper.calls == []


def test_provider_error_event_is_shown(ui: AppTest, fake_wrapper: FakeWrapper) -> None:
    fake_wrapper.error = "Error del proveedor openai: sin crédito"
    fill_form(ui)
    assert "sin crédito" in ui.error[0].value
    assert ui.session_state.history == []


def test_api_down_is_shown(ui: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "stream", refuse)
    fill_form(ui)
    assert "No se puede conectar con la API" in ui.error[0].value


def test_clear_history(ui: AppTest) -> None:
    fill_form(ui)
    ui.sidebar.button[0].click().run()
    assert ui.session_state.history == []


def test_reference_projects_keep_only_complete_rows() -> None:
    table = pd.DataFrame(
        {
            "name": pd.Series(["Web despacho", None, "Portal"], dtype="string"),
            "description": pd.Series(["Web con blog", "Sin nombre", " "], dtype="string"),
            "actual_hours": pd.Series([110, 40, 80], dtype="Int64"),
        }
    )
    assert estimator_client.reference_projects(table) == [
        {"name": "Web despacho", "description": "Web con blog", "actual_hours": 110}
    ]
    assert estimator_client.reference_projects(table.iloc[0:0]) is None


@pytest.mark.parametrize("filename", ["streamlit_app.py", "estimator_client.py"])
def test_ui_does_not_touch_backend_code_or_keys(filename: str) -> None:
    source = (ROOT / filename).read_text()
    assert "from app" not in source and "import app" not in source
    assert "API_KEY" not in source
    assert "dotenv" not in source
