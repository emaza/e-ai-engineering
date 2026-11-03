"""Lógica de negocio: construye el prompt CAG y llama al LLM configurado.

Patrón de mensajes:
    [system]    → instrucciones + ejemplos de estimaciones previas (contexto CAG)
    [user]      → transcripción de la reunión a estimar
    [assistant] → estimación generada por el modelo
"""

import logging
import time
from functools import lru_cache
from typing import Any

import anthropic
import openai

from app.config import Settings
from app.context.examples import HOURLY_RATES, format_examples_for_prompt

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 4000

# USD por 1M de tokens (entrada, salida). Verifica en las páginas oficiales: cambian.
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}


class LLMServiceError(Exception):
    """Error al llamar al proveedor LLM."""


def build_system_prompt() -> str:
    """System prompt: rol + formato de salida + ejemplos inyectados (CAG)."""
    return f"""Eres un consultor senior de proyectos de software con más de 15 años de experiencia en estimación.
Tu tarea es generar la estimación de un nuevo proyecto a partir de la transcripción de una reunión con el cliente.

Usa como referencia las estimaciones de proyectos anteriores que aparecen más abajo: imita su estructura, su nivel de detalle y sus órdenes de magnitud, adaptándolos al proyecto descrito.

Formato obligatorio de la respuesta (Markdown):
- Título "## Estimación: <nombre del proyecto>"
- Tabla de desglose de tareas con las columnas: Tarea | Perfil | Horas | Coste
- Total estimado en horas y coste total (deben coincidir con la suma de la tabla)
- Equipo recomendado
- Duración estimada en semanas
- Supuestos realizados y riesgos principales
- Preguntas abiertas para el cliente si falta información relevante (no la inventes)

Tarifas: {HOURLY_RATES["dev"]:.2f} €/h para desarrollo y {HOURLY_RATES["design"]:.2f} €/h para diseño.

Responde en español.

## Estimaciones de referencia

{format_examples_for_prompt()}"""


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Coste en USD. El modelo puede venir con fecha (gpt-4o-mini-2024-07-18): se busca
    por el prefijo conocido más largo."""
    matches = [key for key in PRICES_PER_1M if model.startswith(key)]
    if not matches:
        return None
    price_in, price_out = PRICES_PER_1M[max(matches, key=len)]
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


@lru_cache
def _openai_client(api_key: str) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=api_key)


@lru_cache
def _anthropic_client(api_key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=api_key)


async def generate_estimation(transcription: str, settings: Settings) -> dict[str, Any]:
    """Genera la estimación con el proveedor configurado en LLM_PROVIDER.

    Ambos proveedores devuelven el mismo dict, así el router no depende del proveedor.
    """
    system_prompt = build_system_prompt()
    logger.info("Generando estimación con %s/%s", settings.LLM_PROVIDER, settings.model)

    start = time.perf_counter()
    try:
        if settings.LLM_PROVIDER == "openai":
            result = await _call_openai(system_prompt, transcription, settings)
        else:
            result = await _call_anthropic(system_prompt, transcription, settings)
    except (openai.APIError, anthropic.APIError) as exc:
        logger.error("Fallo en la llamada a %s: %s", settings.LLM_PROVIDER, exc)
        raise LLMServiceError(f"Error del proveedor {settings.LLM_PROVIDER}: {exc}") from exc

    result["latency_ms"] = round((time.perf_counter() - start) * 1000)
    usage = result["usage"]
    result["estimated_cost_usd"] = estimate_cost(
        result["model"], usage["input_tokens"], usage["output_tokens"]
    )
    if result["finish_reason"] == "length":
        logger.warning("La estimación se cortó al llegar a %s tokens", MAX_OUTPUT_TOKENS)
    logger.info(
        "Estimación generada: %s tokens in / %s out, %s ms",
        usage["input_tokens"],
        usage["output_tokens"],
        result["latency_ms"],
    )
    return result


async def _call_openai(system_prompt: str, transcription: str, settings: Settings) -> dict:
    """OpenAI Responses API: `instructions` es el system prompt e `input` el mensaje de usuario."""
    response = await _openai_client(settings.api_key).responses.create(
        model=settings.model,
        instructions=system_prompt,
        input=[{"role": "user", "content": transcription}],
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    truncated = (
        response.status == "incomplete"
        and response.incomplete_details is not None
        and response.incomplete_details.reason == "max_output_tokens"
    )
    return {
        "estimation": response.output_text,
        "model": response.model,
        "provider": "openai",
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "finish_reason": "length" if truncated else "stop",
    }


async def _call_anthropic(system_prompt: str, transcription: str, settings: Settings) -> dict:
    """Anthropic Messages API: el system prompt va en un parámetro aparte."""
    response = await _anthropic_client(settings.api_key).messages.create(
        model=settings.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": transcription}],
    )
    # La respuesta es una lista de bloques; solo nos interesan los de texto
    # (los modelos con razonamiento añaden bloques `thinking` antes del texto).
    text = "".join(block.text for block in response.content if block.type == "text")
    return {
        "estimation": text,
        "model": response.model,
        "provider": "anthropic",
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        },
        "finish_reason": "length" if response.stop_reason == "max_tokens" else "stop",
    }
