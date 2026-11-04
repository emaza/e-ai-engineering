from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EstimationRequest(BaseModel):
    """Petición con la transcripción de la reunión a estimar."""

    transcription: str = Field(
        ...,
        min_length=20,
        max_length=100_000,
        description="Texto de la transcripción de la reunión con el cliente",
        examples=[
            "En la reunión con el equipo de marketing, el cliente explicó que necesita una "
            "landing page con formulario de contacto, integración con su CRM actual (HubSpot) "
            "y una sección de blog con editor WYSIWYG."
        ],
    )


class TokenUsage(BaseModel):
    """Tokens consumidos en la llamada al LLM."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class EstimationMetadata(BaseModel):
    """Metadatos de la llamada al LLM. En streaming viajan en el evento final `done`."""

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


class EstimationResponse(EstimationMetadata):
    """Estimación generada y metadatos de la llamada."""

    estimation: str = Field(..., description="Estimación generada, en Markdown")
