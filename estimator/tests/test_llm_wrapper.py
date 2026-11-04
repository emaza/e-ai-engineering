"""Tests del wrapper con clientes falsos: parseo de cada SDK, streaming y caché, sin red."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from app.services import llm_wrapper
from app.services.cache import ExactMatchCache
from app.services.llm_wrapper import LLMError, LLMWrapper
from tests.conftest import make_settings


class FakeOpenAI:
    def __init__(self, status: str = "completed") -> None:
        self.kwargs: dict = {}
        self.responses = NS(create=self._create)
        self._status = status

    async def _create(self, **kwargs):
        self.kwargs = kwargs
        return NS(
            output_text="## Estimación: Test",
            model="gpt-4o-mini-2024-07-18",
            status=self._status,
            incomplete_details=NS(reason="max_output_tokens")
            if self._status != "completed"
            else None,
            usage=NS(input_tokens=1000, output_tokens=500, total_tokens=1500),
        )


class FakeAnthropic:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.kwargs: dict = {}
        self.messages = NS(create=self._create)
        self._stop_reason = stop_reason

    async def _create(self, **kwargs):
        self.kwargs = kwargs
        return NS(
            content=[
                NS(type="thinking", thinking=""),
                NS(type="text", text="## Estimación: "),
                NS(type="text", text="Test"),
            ],
            model="claude-haiku-4-5-20251001",
            stop_reason=self._stop_reason,
            usage=NS(input_tokens=1000, output_tokens=500),
        )


SYSTEM = "Eres un estimador de proyectos de software."
MESSAGES = [{"role": "user", "content": "transcripción"}]


def run(coro):
    return asyncio.run(coro)


def complete(settings, cache: ExactMatchCache | None = None) -> dict:
    return run(LLMWrapper(settings, cache).complete(SYSTEM, MESSAGES))


def test_openai_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenAI()
    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: fake)
    result = complete(make_settings())

    assert fake.kwargs["model"] == "gpt-4o-mini"
    assert fake.kwargs["instructions"] == SYSTEM
    assert fake.kwargs["input"] == [{"role": "user", "content": "transcripción"}]
    assert result["estimation"] == "## Estimación: Test"
    assert result["provider"] == "openai"
    assert result["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
    assert result["estimated_cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1e6)
    assert result["latency_ms"] >= 0


def test_anthropic_call_keeps_only_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAnthropic()
    monkeypatch.setattr(llm_wrapper, "_anthropic_client", lambda key: fake)
    settings = make_settings(LLM_PROVIDER="anthropic")
    result = complete(settings)

    assert fake.kwargs["model"] == "claude-haiku-4-5"
    assert fake.kwargs["system"] == SYSTEM
    assert fake.kwargs["messages"] == [{"role": "user", "content": "transcripción"}]
    assert result["estimation"] == "## Estimación: Test"
    assert result["usage"]["total_tokens"] == 1500
    assert result["estimated_cost_usd"] == pytest.approx((1000 * 1.0 + 500 * 5.0) / 1e6)


def test_truncation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: FakeOpenAI("incomplete"))
    monkeypatch.setattr(llm_wrapper, "_anthropic_client", lambda key: FakeAnthropic("max_tokens"))
    for provider in ("openai", "anthropic"):
        settings = make_settings(LLM_PROVIDER=provider)
        result = complete(settings)
        assert result["finish_reason"] == "length"


def test_sdk_errors_become_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    async def boom(**kwargs):
        raise openai.APIConnectionError(request=NS(method="POST", url="https://api.openai.com"))

    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: NS(responses=NS(create=boom)))
    with pytest.raises(LLMError, match="openai"):
        complete(make_settings())


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o-mini-2024-07-18", (1e6 * 0.15 + 1e6 * 0.60) / 1e6),
        ("claude-haiku-4-5-20251001", 6.0),
        ("modelo-desconocido", None),
    ],
)
def test_estimate_cost(model: str, expected: float | None) -> None:
    assert llm_wrapper.estimate_cost(model, 1_000_000, 1_000_000) == expected


# --- Streaming (LLMStream) -------------------------------------------------------


class FakeOpenAIStream:
    def __init__(self, final_type: str = "response.completed") -> None:
        self.kwargs: dict = {}
        self.responses = NS(create=self._create)
        self._final_type = final_type

    async def _create(self, **kwargs):
        self.kwargs = kwargs
        final = NS(
            type=self._final_type,
            response=NS(
                model="gpt-4o-mini-2024-07-18",
                status="incomplete" if self._final_type == "response.incomplete" else "completed",
                incomplete_details=NS(reason="max_output_tokens"),
                usage=NS(input_tokens=1000, output_tokens=500, total_tokens=1500),
            ),
        )
        return self._events(final)

    async def _events(self, final):
        yield NS(type="response.created")
        for delta in ("## Estimación", ": ", "Test"):
            yield NS(type="response.output_text.delta", delta=delta)
        yield final


class FakeAnthropicStream:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.kwargs: dict = {}
        self.messages = NS(stream=self._stream)
        self._stop_reason = stop_reason

    def _stream(self, **kwargs):
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    @property
    def text_stream(self):
        async def chunks():
            for text in ("## Estimación", ": ", "Test"):
                yield text

        return chunks()

    async def get_final_message(self):
        return NS(
            model="claude-haiku-4-5-20251001",
            stop_reason=self._stop_reason,
            usage=NS(input_tokens=1000, output_tokens=500),
        )


def consume(stream) -> list[str]:
    async def collect():
        return [chunk async for chunk in stream]

    return run(collect())


def test_openai_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenAIStream()
    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: fake)
    stream = LLMWrapper(make_settings()).stream(SYSTEM, MESSAGES)

    assert consume(stream) == ["## Estimación", ": ", "Test"]
    assert fake.kwargs["stream"] is True
    assert fake.kwargs["instructions"] == SYSTEM
    assert fake.kwargs["input"] == MESSAGES
    result = stream.result
    assert result["estimation"] == "## Estimación: Test"
    assert result["model"] == "gpt-4o-mini-2024-07-18"
    assert result["usage"] == {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
    assert result["finish_reason"] == "stop"
    assert result["estimated_cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1e6)
    assert result["latency_ms"] >= 0


def test_anthropic_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAnthropicStream()
    monkeypatch.setattr(llm_wrapper, "_anthropic_client", lambda key: fake)
    stream = LLMWrapper(make_settings(LLM_PROVIDER="anthropic")).stream(SYSTEM, MESSAGES)

    assert "".join(consume(stream)) == "## Estimación: Test"
    assert fake.kwargs["system"] == SYSTEM
    assert fake.kwargs["messages"] == MESSAGES
    assert stream.result["provider"] == "anthropic"
    assert stream.result["usage"]["total_tokens"] == 1500
    assert stream.result["finish_reason"] == "stop"


def test_stream_truncation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_wrapper, "_openai_client", lambda key: FakeOpenAIStream("response.incomplete")
    )
    monkeypatch.setattr(
        llm_wrapper, "_anthropic_client", lambda key: FakeAnthropicStream("max_tokens")
    )
    for provider in ("openai", "anthropic"):
        stream = LLMWrapper(make_settings(LLM_PROVIDER=provider)).stream(SYSTEM, MESSAGES)
        consume(stream)
        assert stream.result["finish_reason"] == "length"


def test_stream_failures_become_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    async def boom(**kwargs):
        raise openai.APIConnectionError(request=NS(method="POST", url="https://api.openai.com"))

    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: NS(responses=NS(create=boom)))
    with pytest.raises(LLMError, match="openai"):
        consume(LLMWrapper(make_settings()).stream(SYSTEM, MESSAGES))

    monkeypatch.setattr(
        llm_wrapper, "_openai_client", lambda key: FakeOpenAIStream("response.failed")
    )
    stream = LLMWrapper(make_settings()).stream(SYSTEM, MESSAGES)
    with pytest.raises(LLMError, match="streaming"):
        consume(stream)
    assert stream.result is None


# --- Caché exact-match -------------------------------------------------------------------


def test_cache_hit_skips_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenAI()
    calls = []

    async def counting_create(**kwargs):
        calls.append(kwargs)
        return await fake._create(**kwargs)

    monkeypatch.setattr(
        llm_wrapper, "_openai_client", lambda key: NS(responses=NS(create=counting_create))
    )
    cache = ExactMatchCache(ttl_seconds=60, max_entries=10)

    first = complete(make_settings(), cache)
    second = complete(make_settings(), cache)

    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["estimation"] == first["estimation"]
    assert second["estimated_cost_usd"] == 0.0  # de caché no cuesta nada
    assert second["usage"] == first["usage"]  # tokens de la llamada original


def test_truncated_responses_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: FakeOpenAI("incomplete"))
    cache = ExactMatchCache(ttl_seconds=60, max_entries=10)
    complete(make_settings(), cache)
    assert len(cache) == 0


def test_stream_uses_and_fills_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: FakeOpenAIStream())
    wrapper = LLMWrapper(make_settings(), ExactMatchCache(ttl_seconds=60, max_entries=10))

    first = wrapper.stream(SYSTEM, MESSAGES)
    assert consume(first) == ["## Estimación", ": ", "Test"]
    assert first.result["cached"] is False

    # El segundo stream sale de caché: el texto completo en un solo trozo
    monkeypatch.setattr(llm_wrapper, "_openai_client", lambda key: pytest.fail("sin caché"))
    second = wrapper.stream(SYSTEM, MESSAGES)
    assert consume(second) == ["## Estimación: Test"]
    assert second.result["cached"] is True

    # Y lo que guardó el stream también lo aprovecha complete()
    assert run(wrapper.complete(SYSTEM, MESSAGES))["cached"] is True
