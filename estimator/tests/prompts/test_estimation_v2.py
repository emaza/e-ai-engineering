"""Tests de la versión v2 (estimador conservador) y del versionado de prompts."""

import typing

from app.prompts.loader import available_versions, render_estimation_prompt
from app.schemas import EstimationRequest, PromptVersion

REQUEST = EstimationRequest(
    description="Herramienta interna para que el equipo comercial registre visitas a clientes.",
    project_type="internal_tool",
    detail_level="medium",
    output_format="phases_table",
)


def test_every_declared_version_has_its_templates() -> None:
    assert sorted(typing.get_args(PromptVersion)) == available_versions()


def test_v2_asks_for_ranges_and_contingency() -> None:
    v1, _ = render_estimation_prompt(REQUEST, "v1")
    v2, _ = render_estimation_prompt(REQUEST, "v2")
    for marker in (
        "optimista / probable / pesimista",
        "contingencia del 15 %",
        "resumen ejecutivo",
    ):
        assert marker in v2
        assert marker not in v1


def test_v2_examples_include_contingency_line() -> None:
    v2, _ = render_estimation_prompt(REQUEST, "v2")
    assert "**Contingencia (15 %): 3.731,25 €**" in v2  # 15 % de los 24.875 € del gimnasio
    assert "**Coste total con contingencia: 28.606,25 €**" in v2


def test_user_prompt_is_the_same_in_both_versions() -> None:
    # La variación de v2 está en las instrucciones, no en cómo se envían los datos
    assert render_estimation_prompt(REQUEST, "v1")[1] == render_estimation_prompt(REQUEST, "v2")[1]
