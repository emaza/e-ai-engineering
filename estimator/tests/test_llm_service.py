"""Tests del servicio con clientes falsos: comprueban el parseo de cada SDK sin red."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from app.services import llm_service
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


def run(coro):
    return asyncio.run(coro)


def test_openai_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenAI()
    monkeypatch.setattr(llm_service, "_openai_client", lambda key: fake)
    result = run(llm_service.generate_estimation("transcripción", make_settings()))

    assert fake.kwargs["model"] == "gpt-4o-mini"
    assert fake.kwargs["instructions"] == llm_service.build_system_prompt()
    assert fake.kwargs["input"] == [{"role": "user", "content": "transcripción"}]
    assert result["estimation"] == "## Estimación: Test"
    assert result["provider"] == "openai"
    assert result["finish_reason"] == "stop"
    assert result["usage"] == {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
    assert result["estimated_cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1e6)
    assert result["latency_ms"] >= 0


def test_anthropic_call_keeps_only_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAnthropic()
    monkeypatch.setattr(llm_service, "_anthropic_client", lambda key: fake)
    settings = make_settings(LLM_PROVIDER="anthropic")
    result = run(llm_service.generate_estimation("transcripción", settings))

    assert fake.kwargs["model"] == "claude-haiku-4-5"
    assert fake.kwargs["system"] == llm_service.build_system_prompt()
    assert fake.kwargs["messages"] == [{"role": "user", "content": "transcripción"}]
    assert result["estimation"] == "## Estimación: Test"
    assert result["usage"]["total_tokens"] == 1500
    assert result["estimated_cost_usd"] == pytest.approx((1000 * 1.0 + 500 * 5.0) / 1e6)


def test_truncation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_service, "_openai_client", lambda key: FakeOpenAI("incomplete"))
    monkeypatch.setattr(llm_service, "_anthropic_client", lambda key: FakeAnthropic("max_tokens"))
    for provider in ("openai", "anthropic"):
        settings = make_settings(LLM_PROVIDER=provider)
        result = run(llm_service.generate_estimation("transcripción", settings))
        assert result["finish_reason"] == "length"


def test_sdk_errors_become_llm_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    async def boom(**kwargs):
        raise openai.APIConnectionError(request=NS(method="POST", url="https://api.openai.com"))

    monkeypatch.setattr(llm_service, "_openai_client", lambda key: NS(responses=NS(create=boom)))
    with pytest.raises(llm_service.LLMServiceError, match="openai"):
        run(llm_service.generate_estimation("transcripción", make_settings()))


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o-mini-2024-07-18", (1e6 * 0.15 + 1e6 * 0.60) / 1e6),
        ("claude-haiku-4-5-20251001", 6.0),
        ("modelo-desconocido", None),
    ],
)
def test_estimate_cost(model: str, expected: float | None) -> None:
    assert llm_service.estimate_cost(model, 1_000_000, 1_000_000) == expected


# --- Streaming (EstimationStream) -------------------------------------------------------

HISTORY = [
    {"role": "user", "content": "transcripción"},
    {"role": "assistant", "content": "## Estimación: Test"},
    {"role": "user", "content": "quita el diseñador"},
]


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


def consume(stream: llm_service.EstimationStream) -> list[str]:
    async def collect():
        return [chunk async for chunk in stream]

    return run(collect())


def test_openai_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOpenAIStream()
    monkeypatch.setattr(llm_service, "_openai_client", lambda key: fake)
    stream = llm_service.EstimationStream(HISTORY, make_settings())

    assert consume(stream) == ["## Estimación", ": ", "Test"]
    assert fake.kwargs["stream"] is True
    assert fake.kwargs["instructions"] == llm_service.build_system_prompt()
    assert fake.kwargs["input"] == HISTORY
    result = stream.result
    assert result["estimation"] == "## Estimación: Test"
    assert result["model"] == "gpt-4o-mini-2024-07-18"
    assert result["usage"] == {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
    assert result["finish_reason"] == "stop"
    assert result["estimated_cost_usd"] == pytest.approx((1000 * 0.15 + 500 * 0.60) / 1e6)
    assert result["latency_ms"] >= 0


def test_anthropic_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeAnthropicStream()
    monkeypatch.setattr(llm_service, "_anthropic_client", lambda key: fake)
    stream = llm_service.EstimationStream(HISTORY, make_settings(LLM_PROVIDER="anthropic"))

    assert "".join(consume(stream)) == "## Estimación: Test"
    assert fake.kwargs["system"] == llm_service.build_system_prompt()
    assert fake.kwargs["messages"] == HISTORY
    assert stream.result["provider"] == "anthropic"
    assert stream.result["usage"]["total_tokens"] == 1500
    assert stream.result["finish_reason"] == "stop"


def test_stream_truncation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_service, "_openai_client", lambda key: FakeOpenAIStream("response.incomplete")
    )
    monkeypatch.setattr(
        llm_service, "_anthropic_client", lambda key: FakeAnthropicStream("max_tokens")
    )
    for provider in ("openai", "anthropic"):
        stream = llm_service.EstimationStream(HISTORY, make_settings(LLM_PROVIDER=provider))
        consume(stream)
        assert stream.result["finish_reason"] == "length"


def test_stream_failures_become_llm_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    async def boom(**kwargs):
        raise openai.APIConnectionError(request=NS(method="POST", url="https://api.openai.com"))

    monkeypatch.setattr(llm_service, "_openai_client", lambda key: NS(responses=NS(create=boom)))
    with pytest.raises(llm_service.LLMServiceError, match="openai"):
        consume(llm_service.EstimationStream(HISTORY, make_settings()))

    monkeypatch.setattr(
        llm_service, "_openai_client", lambda key: FakeOpenAIStream("response.failed")
    )
    stream = llm_service.EstimationStream(HISTORY, make_settings())
    with pytest.raises(llm_service.LLMServiceError, match="streaming"):
        consume(stream)
    assert stream.result is None
