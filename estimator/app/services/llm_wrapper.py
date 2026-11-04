"""Wrapper de proveedores LLM: una sola interfaz para OpenAI y Anthropic.

El resto de la aplicación solo ve `LLMWrapper.complete()` y `LLMWrapper.stream()`, que
reciben el system prompt y los mensajes por separado y devuelven siempre el mismo dict,
sea cual sea el proveedor. Aquí viven también la caché exact-match y los logs de cada
llamada (tokens, latencia, coste).
"""

import time
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, Any

import anthropic
import openai
import structlog
from fastapi import Depends

from app.config import Settings, SettingsDep
from app.services.cache import ExactMatchCache

logger = structlog.get_logger(__name__)

Messages = list[dict[str, str]]

MAX_OUTPUT_TOKENS = 4000

# USD por 1M de tokens (entrada, salida). Verifica en las páginas oficiales: cambian.
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5": (1.00, 5.00),
}


class LLMError(Exception):
    """Error al llamar al proveedor LLM."""


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Coste en USD. El modelo puede venir con fecha (gpt-4o-mini-2024-07-18): se busca
    por el prefijo conocido más largo."""
    matches = [key for key in PRICES_PER_1M if model.startswith(key)]
    if not matches:
        return None
    price_in, price_out = PRICES_PER_1M[max(matches, key=len)]
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


class LLMWrapper:
    """Llama al proveedor configurado en LLM_PROVIDER, con caché opcional."""

    def __init__(self, settings: Settings, cache: ExactMatchCache | None = None) -> None:
        self.settings = settings
        self.cache = cache

    async def complete(self, system: str, messages: Messages) -> dict[str, Any]:
        """Respuesta completa: estimation, model, provider, usage, finish_reason,
        latency_ms, estimated_cost_usd y cached."""
        start = time.perf_counter()
        key = self._cache_key(system, messages)
        if (cached := self._cache_get(key)) is not None:
            return self._finalize(cached, start, cached=True)

        try:
            if self.settings.LLM_PROVIDER == "openai":
                result = await _call_openai(system, messages, self.settings)
            else:
                result = await _call_anthropic(system, messages, self.settings)
        except (openai.APIError, anthropic.APIError) as exc:
            raise self._provider_error(exc) from exc

        self._cache_set(key, result)
        return self._finalize(result, start, cached=False)

    def stream(self, system: str, messages: Messages) -> "LLMStream":
        """Respuesta en streaming: iterable async de trozos de texto con `.result` al final."""
        return LLMStream(self, system, messages)

    # --- Caché -------------------------------------------------------------------------

    def _cache_key(self, system: str, messages: Messages) -> str | None:
        if self.cache is None:
            return None
        return self.cache.make_key(
            provider=self.settings.LLM_PROVIDER,
            model=self.settings.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=messages,
        )

    def _cache_get(self, key: str | None) -> dict[str, Any] | None:
        if key is None:
            return None
        cached = self.cache.get(key)
        logger.debug("cache_hit" if cached else "cache_miss", key=key[:12])
        return cached

    def _cache_set(self, key: str | None, result: dict[str, Any]) -> None:
        # Una respuesta cortada por el límite de tokens no merece repetirse
        if key is not None and result["finish_reason"] == "stop":
            self.cache.set(key, result)

    # --- Común ---------------------------------------------------------------------------

    def _provider_error(self, exc: Exception) -> LLMError:
        logger.error(
            "llm_call_failed",
            provider=self.settings.LLM_PROVIDER,
            model=self.settings.model,
            error=str(exc),
        )
        return LLMError(f"Error del proveedor {self.settings.LLM_PROVIDER}: {exc}")

    def _finalize(self, result: dict[str, Any], start: float, *, cached: bool) -> dict[str, Any]:
        """Añade latencia, coste y `cached`, y registra la llamada."""
        result = {**result, "cached": cached}
        result["latency_ms"] = round((time.perf_counter() - start) * 1000)
        usage = result["usage"]
        # Una respuesta de caché no ha costado nada; los tokens son los de la llamada original
        result["estimated_cost_usd"] = (
            0.0
            if cached
            else estimate_cost(result["model"], usage["input_tokens"], usage["output_tokens"])
        )
        if result["finish_reason"] == "length":
            logger.warning("llm_response_truncated", max_output_tokens=MAX_OUTPUT_TOKENS)
        logger.info(
            "llm_call_completed",
            provider=result["provider"],
            model=result["model"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=result["latency_ms"],
            cost_usd=result["estimated_cost_usd"],
            finish_reason=result["finish_reason"],
            cached=cached,
        )
        return result


class LLMStream:
    """Iterable async de trozos de texto. Al terminar, `result` tiene el mismo dict que
    `LLMWrapper.complete()`. Con acierto en caché, el texto guardado sale en un solo trozo."""

    def __init__(self, wrapper: LLMWrapper, system: str, messages: Messages) -> None:
        self._wrapper = wrapper
        self._system = system
        self._messages = messages
        self.result: dict[str, Any] | None = None

    async def __aiter__(self) -> AsyncIterator[str]:
        wrapper, settings = self._wrapper, self._wrapper.settings
        start = time.perf_counter()
        key = wrapper._cache_key(self._system, self._messages)
        if (cached := wrapper._cache_get(key)) is not None:
            yield cached["estimation"]
            self.result = wrapper._finalize(cached, start, cached=True)
            return

        meta: dict[str, Any] = {}
        chunks: list[str] = []
        stream = _stream_openai if settings.LLM_PROVIDER == "openai" else _stream_anthropic
        try:
            async for text in stream(self._system, self._messages, settings, meta):
                chunks.append(text)
                yield text
        except (openai.APIError, anthropic.APIError) as exc:
            raise wrapper._provider_error(exc) from exc

        if not meta:
            raise LLMError(f"El streaming de {settings.LLM_PROVIDER} terminó sin metadatos")
        result = {"estimation": "".join(chunks), **meta}
        wrapper._cache_set(key, result)
        self.result = wrapper._finalize(result, start, cached=False)


@lru_cache
def _shared_cache(ttl_seconds: int, max_entries: int) -> ExactMatchCache:
    return ExactMatchCache(ttl_seconds=ttl_seconds, max_entries=max_entries)


def get_llm_wrapper(settings: SettingsDep) -> LLMWrapper:
    """Dependencia de FastAPI. La caché es compartida por todo el proceso."""
    cache = (
        _shared_cache(settings.CACHE_TTL_SECONDS, settings.CACHE_MAX_ENTRIES)
        if settings.CACHE_ENABLED
        else None
    )
    return LLMWrapper(settings, cache)


LLMWrapperDep = Annotated[LLMWrapper, Depends(get_llm_wrapper)]


# =============================================================================
# Adaptadores por proveedor
# =============================================================================


@lru_cache
def _openai_client(api_key: str) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=api_key)


@lru_cache
def _anthropic_client(api_key: str) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=api_key)


async def _call_openai(system: str, messages: Messages, settings: Settings) -> dict:
    """OpenAI Responses API: `instructions` es el system prompt e `input` los mensajes."""
    response = await _openai_client(settings.api_key).responses.create(
        model=settings.model,
        instructions=system,
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
    system: str, messages: Messages, settings: Settings, meta: dict[str, Any]
) -> AsyncIterator[str]:
    """Con `stream=True` la Responses API emite eventos: el texto llega en los
    `response.output_text.delta` y el uso de tokens en el evento final."""
    stream = await _openai_client(settings.api_key).responses.create(
        model=settings.model,
        instructions=system,
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
            raise LLMError(f"Error del proveedor openai durante el streaming: {event}")


async def _call_anthropic(system: str, messages: Messages, settings: Settings) -> dict:
    """Anthropic Messages API: el system prompt va en un parámetro aparte."""
    response = await _anthropic_client(settings.api_key).messages.create(
        model=settings.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
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
    system: str, messages: Messages, settings: Settings, meta: dict[str, Any]
) -> AsyncIterator[str]:
    """`messages.stream` acumula el mensaje: `text_stream` da solo los deltas de texto y
    `get_final_message()` el mensaje completo con el uso de tokens."""
    async with _anthropic_client(settings.api_key).messages.stream(
        model=settings.model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text
        meta.update(_anthropic_meta(await stream.get_final_message()))
