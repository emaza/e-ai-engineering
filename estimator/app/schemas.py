"""Contrato entre el cliente y el servicio IA.

El cliente ya no envía texto libre: envía parámetros tipados (tipo de proyecto, nivel de
detalle, formato de salida) que el servicio convierte en prompt con los templates Jinja2.
Los enums se serializan como su valor ("mobile_app", no "MOBILE_APP").
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Versiones de prompt disponibles: cada una es una carpeta en app/prompts/estimation/
PromptVersion = Literal["v1", "v2"]


# StrEnum (Python 3.11+) equivale al `class X(str, Enum)` del enunciado: se serializa como
# su valor y además str(ProjectType.MOBILE_APP) == "mobile_app".
class ProjectType(StrEnum):
    MOBILE_APP = "mobile_app"
    WEB_SAAS = "web_saas"
    INTERNAL_TOOL = "internal_tool"
    DATA_PIPELINE = "data_pipeline"


class DetailLevel(StrEnum):
    SUMMARY = "summary"
    MEDIUM = "medium"
    DETAILED = "detailed"


class OutputFormat(StrEnum):
    PHASES_TABLE = "phases_table"
    LINE_ITEMS = "line_items"
    NARRATIVE = "narrative"


class ReferenceProject(BaseModel):
    """Proyecto similar ya entregado: le da al modelo un orden de magnitud real."""

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=500)
    actual_hours: int = Field(..., gt=0, description="Horas que costó realmente")


class EstimationRequest(BaseModel):
    """Parámetros de la estimación."""

    description: str = Field(
        ...,
        min_length=20,
        max_length=2000,
        description="Descripción del proyecto (resumen de la reunión con el cliente)",
        examples=[
            "Landing page con formulario de contacto, integración con el CRM del cliente "
            "(HubSpot) y un blog con editor WYSIWYG. El diseño ya existe en Figma."
        ],
    )
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
    reference_projects: list[ReferenceProject] | None = Field(None, max_length=5)


class TokenUsage(BaseModel):
    """Tokens consumidos en la llamada al LLM."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class EstimationMetadata(BaseModel):
    """Métricas de la llamada al LLM."""

    model: str = Field(..., description="Modelo que generó la estimación")
    provider: Literal["openai", "anthropic"]
    usage: TokenUsage
    finish_reason: Literal["stop", "length"] = Field(
        ..., description="'length' indica que la respuesta se cortó por el límite de tokens"
    )
    latency_ms: int = Field(..., description="Duración de la llamada al LLM")
    estimated_cost_usd: float | None = Field(
        None, description="Coste estimado según la tabla de precios (None si el modelo no está)"
    )
    cached: bool = Field(..., description="True si la respuesta salió de la caché exact-match")
    created_at: datetime


class EstimationResult(BaseModel):
    """Todo menos el texto. En streaming es el `data` del evento final `done`."""

    prompt_version: PromptVersion
    metadata: EstimationMetadata


class EstimationResponse(EstimationResult):
    """Estimación generada (texto libre por ahora), versión del prompt y métricas."""

    text: str = Field(..., description="Estimación generada, en Markdown")
