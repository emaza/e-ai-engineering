"""Lógica de negocio: construye el prompt CAG y llama al LLM configurado.

Patrón de mensajes:
    [system]    → instrucciones + ejemplos de estimaciones previas (contexto CAG)
    [user]      → transcripción de la reunión a estimar
    [assistant] → estimación generada por el modelo
    ...         → en el chat (EstimationStream) siguen los turnos de ajuste del usuario
"""

import logging
import time
from collections.abc import AsyncIterator
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


Messages = list[dict[str, str]]


async def generate_estimation(transcription: str, settings: Settings) -> dict[str, Any]:
    """Genera la estimación con el proveedor configurado en LLM_PROVIDER.

    Ambos proveedores devuelven el mismo dict, así el router no depende del proveedor.
    """
    messages = [{"role": "user", "content": transcription}]
    system_prompt = build_system_prompt()
    logger.info("Generando estimación con %s/%s", settings.LLM_PROVIDER, settings.model)

    start = time.perf_counter()
    try:
        if settings.LLM_PROVIDER == "openai":
            result = await _call_openai(system_prompt, messages, settings)
        else:
            result = await _call_anthropic(system_prompt, messages, settings)
    except (openai.APIError, anthropic.APIError) as exc:
        raise _provider_error(settings, exc) from exc

    return _finalize(result, start)


class EstimationStream:
    """Estimación en streaming: al iterarla da los trozos de texto según llegan.

    Recibe el historial completo de la conversación (turnos user/assistant), así el usuario
    puede pedir ajustes sobre una estimación anterior. Al terminar la iteración, `result`
    contiene el mismo dict que `generate_estimation`.
    """

    def __init__(self, messages: Messages, settings: Settings) -> None:
        self.messages = messages
        self.settings = settings
        self.result: dict[str, Any] | None = None

    async def __aiter__(self) -> AsyncIterator[str]:
        settings = self.settings
        system_prompt = build_system_prompt()
        logger.info(
            "Generando estimación en streaming con %s/%s (%s mensajes)",
            settings.LLM_PROVIDER,
            settings.model,
            len(self.messages),
        )

        start = time.perf_counter()
        meta: dict[str, Any] = {}
        chunks: list[str] = []
        stream = _stream_openai if settings.LLM_PROVIDER == "openai" else _stream_anthropic
        try:
            async for text in stream(system_prompt, self.messages, settings, meta):
                chunks.append(text)
                yield text
        except (openai.APIError, anthropic.APIError) as exc:
            raise _provider_error(settings, exc) from exc

        if not meta:
            raise LLMServiceError(f"El streaming de {settings.LLM_PROVIDER} terminó sin metadatos")
        self.result = _finalize({"estimation": "".join(chunks), **meta}, start)


def _provider_error(settings: Settings, exc: Exception) -> LLMServiceError:
    logger.error("Fallo en la llamada a %s: %s", settings.LLM_PROVIDER, exc)
    return LLMServiceError(f"Error del proveedor {settings.LLM_PROVIDER}: {exc}")


def _finalize(result: dict[str, Any], start: float) -> dict[str, Any]:
    """Añade latencia y coste a la respuesta del proveedor y registra la llamada."""
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


async def _call_openai(system_prompt: str, messages: Messages, settings: Settings) -> dict:
    """OpenAI Responses API: `instructions` es el system prompt e `input` el mensaje de usuario."""
    response = await _openai_client(settings.api_key).responses.create(
        model=settings.model,
        instructions=system_prompt,
        input=messages,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    return {"estimation": response.output_text, **_openai_meta(response)}


def _openai_meta(response: Any) -> dict[str, Any]:
    """Modelo, uso de tokens y motivo de fin de una respuesta de la Responses API."""
    truncated = (
        response.status == "incomplete"
        and response.incomplete_details is not None
        and response.incomplete_details.reason == "max_output_tokens"
    )
    return {
        "model": response.model,
        "provider": "openai",
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "finish_reason": "length" if truncated else "stop",
    }


async def _stream_openai(
    system_prompt: str, messages: Messages, settings: Settings, meta: dict[str, Any]
) -> AsyncIterator[str]:
    """Con `stream=True` la Responses API emite eventos: el texto llega en los
    `response.output_text.delta` y el uso de tokens en el evento final."""
    stream = await _openai_client(settings.api_key).responses.create(
        model=settings.model,
        instructions=system_prompt,
        input=messages,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        stream=True,
    )
    async for event in stream:
        if event.type == "response.output_text.delta":
            yield event.delta
        elif event.type in ("response.completed", "response.incomplete"):
            meta.update(_openai_meta(event.response))
        elif event.type in ("response.failed", "error"):
            raise LLMServiceError(f"Error del proveedor openai durante el streaming: {event}")


async def _call_anthropic(system_prompt: str, messages: Messages, settings: Settings) -> dict:
    """Anthropic Messages API: el system prompt va en un parámetro aparte."""
    response = await _anthropic_client(settings.api_key).messages.create(
        model=settings.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=messages,
    )
    # La respuesta es una lista de bloques; solo nos interesan los de texto
    # (los modelos con razonamiento añaden bloques `thinking` antes del texto).
    text = "".join(block.text for block in response.content if block.type == "text")
    return {"estimation": text, **_anthropic_meta(response)}


def _anthropic_meta(message: Any) -> dict[str, Any]:
    """Modelo, uso de tokens y motivo de fin de un mensaje de Anthropic."""
    return {
        "model": message.model,
        "provider": "anthropic",
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "total_tokens": message.usage.input_tokens + message.usage.output_tokens,
        },
        "finish_reason": "length" if message.stop_reason == "max_tokens" else "stop",
    }


async def _stream_anthropic(
    system_prompt: str, messages: Messages, settings: Settings, meta: dict[str, Any]
) -> AsyncIterator[str]:
    """`messages.stream` acumula el mensaje: `text_stream` da solo los deltas de texto y
    `get_final_message()` el mensaje completo con el uso de tokens."""
    async with _anthropic_client(settings.api_key).messages.stream(
        model=settings.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text
        meta.update(_anthropic_meta(await stream.get_final_message()))
