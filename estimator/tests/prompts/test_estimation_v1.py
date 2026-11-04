"""Tests del template de estimación v1: comprueban el texto que se envía al modelo.

Son tests del template, no del modelo: no llaman a ninguna API y tardan milisegundos.
Verifican que cada parámetro del formulario activa (o no) su bloque de instrucciones;
no pueden verificar que el modelo luego las cumpla.
"""

import itertools
import re

import pytest
from jinja2 import UndefinedError
from structlog.testing import capture_logs

from app.prompts.loader import HOURLY_RATES, get_environment, render_estimation_prompt
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType

DESCRIPTION = (
    "Marketplace B2B para que talleres mecánicos compren recambios a varios distribuidores, "
    "con catálogo unificado, pedidos y facturación mensual."
)


def make_request(**overrides) -> EstimationRequest:
    values = {
        "description": DESCRIPTION,
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "phases_table",
    } | overrides
    return EstimationRequest(**values)


def render(**overrides) -> tuple[str, str]:
    return render_estimation_prompt(make_request(**overrides), version="v1")


# --- Los tres tests que pide el enunciado ----------------------------------------------


def test_description_is_inside_project_description_block() -> None:
    system, user = render()
    block = re.search(r"<project_description>(.*?)</project_description>", user, re.DOTALL)
    assert block is not None
    assert block.group(1).strip() == DESCRIPTION
    assert DESCRIPTION not in system  # los datos del cliente van en el user, no en el system


def test_output_format_controls_confidence_column() -> None:
    phases_system, _ = render(output_format="phases_table")
    narrative_system, _ = render(output_format="narrative")
    assert "confidence_pct" in phases_system
    assert "confidence_pct" not in narrative_system


def test_detailed_level_asks_for_assumptions_per_phase() -> None:
    detailed_system, _ = render(detail_level="detailed")
    summary_system, _ = render(detail_level="summary")
    assert "Lista las asunciones de cada fase" in detailed_system
    assert "Lista las asunciones de cada fase" not in summary_system
    assert "No añadas supuestos" in summary_system


# --- Cobertura adicional ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("project_type", "detail_level", "output_format"),
    list(itertools.product(ProjectType, DetailLevel, OutputFormat)),
)
def test_every_combination_renders(project_type, detail_level, output_format) -> None:
    system, user = render(
        project_type=project_type, detail_level=detail_level, output_format=output_format
    )
    assert system.startswith("Eres un consultor senior")
    assert f"<project_type>{project_type.value}</project_type>" in user
    assert "{{" not in system + user and "{%" not in system + user  # nada sin renderizar
    assert "\n\n\n" not in system  # trim_blocks/lstrip_blocks: sin huecos de las etiquetas


def test_line_items_and_narrative_have_their_own_instructions() -> None:
    line_items, _ = render(output_format="line_items")
    narrative, _ = render(output_format="narrative")
    assert "Tarea | Perfil | Horas | Coste" in line_items
    assert "sin tablas" in narrative
    assert "| Tarea |" not in narrative and "| Fase |" not in narrative  # ni en los ejemplos


def test_project_type_adds_its_considerations() -> None:
    # Se busca la frase de la instrucción: los ejemplos también hablan de stores y pipelines
    assert "Es una app móvil: considera" in render(project_type="mobile_app")[0]
    assert "Es un pipeline de datos: considera" in render(project_type="data_pipeline")[0]
    assert "Es una app móvil: considera" not in render(project_type="data_pipeline")[0]


def test_examples_follow_requested_format_and_totals_add_up() -> None:
    system, _ = render(output_format="line_items")
    gym = system[system.index("App móvil de reservas") : system.index("**Equipo recomendado")]
    rows = re.findall(r"^\| [^|]+ \| (Desarrollo|Diseño) \| (\d+) \|", gym, re.MULTILINE)
    hours = sum(int(h) for _, h in rows)
    cost = sum(int(h) * HOURLY_RATES["dev" if r == "Desarrollo" else "design"] for r, h in rows)
    assert hours == 410
    assert f"**Total estimado: {hours} horas**" in gym
    assert "**Coste total: 24.875,00 €**" in gym and cost == 24_875


def test_reference_projects_are_listed_only_when_present() -> None:
    _, without = render()
    assert "<reference_projects>" not in without

    _, user = render(
        reference_projects=[
            {
                "name": "Portal de proveedores",
                "description": "Catálogo y pedidos",
                "actual_hours": 320,
            },
            {"name": "Tienda B2B", "description": "Precios por cliente", "actual_hours": 280},
        ]
    )
    block = user[user.index("<reference_projects>") : user.index("</reference_projects>")]
    assert "- Portal de proveedores (320 horas reales): Catálogo y pedidos" in block
    assert "- Tienda B2B (280 horas reales): Precios por cliente" in block


def test_missing_variable_fails_loudly() -> None:
    # StrictUndefined: si falta una variable, error en vez de un hueco silencioso
    template = get_environment().get_template("estimation/v1/user.j2")
    with pytest.raises(UndefinedError):
        template.render(project_type="web_saas")  # falta description


def test_unknown_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="v9"):
        render_estimation_prompt(make_request(), version="v9")


def test_render_is_logged_with_version_and_hash() -> None:
    with capture_logs() as logs:
        system, _ = render()
    event = next(log for log in logs if log["event"] == "prompt_rendered")
    assert event["prompt_version"] == "v1"
    assert len(event["system_sha256"]) == 12
    assert event["system_chars"] == len(system)

    # El hash identifica el contenido: cambia si cambia el prompt
    with capture_logs() as logs:
        render(output_format="narrative")
    other = next(log for log in logs if log["event"] == "prompt_rendered")
    assert other["system_sha256"] != event["system_sha256"]
