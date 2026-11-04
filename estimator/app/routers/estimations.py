from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.schemas import (
    EstimationMetadata,
    EstimationRequest,
    EstimationResponse,
    EstimationResult,
    PromptVersion,
)
from app.services.llm_service import generate_estimation, stream_estimation
from app.services.llm_wrapper import LLMError, LLMWrapperDep

router = APIRouter(tags=["estimations"])
logger = structlog.get_logger(__name__)

PromptVersionQuery = Annotated[
    PromptVersion, Query(description="Versión del prompt (carpeta en app/prompts/estimation/)")
]


def _bind_request_context(request: EstimationRequest, version: PromptVersion) -> None:
    """Añade los parámetros de la estimación a todos los logs del request."""
    structlog.contextvars.bind_contextvars(
        prompt_version=version,
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
    )


def _metadata(result: dict[str, Any]) -> EstimationMetadata:
    return EstimationMetadata(
        **{k: v for k, v in result.items() if k != "estimation"}, created_at=datetime.now(UTC)
    )


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(
    request: EstimationRequest,
    wrapper: LLMWrapperDep,
    prompt_version: PromptVersionQuery = "v1",
) -> EstimationResponse:
    """Genera la estimación de un proyecto a partir de sus parámetros tipados."""
    _bind_request_context(request, prompt_version)
    try:
        result = await generate_estimation(request, wrapper, prompt_version)
    except LLMError as exc:
        # El fallo es del proveedor externo, no de nuestro servicio: 502 Bad Gateway
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return EstimationResponse(
        text=result["estimation"], prompt_version=prompt_version, metadata=_metadata(result)
    )


@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(
    request: EstimationRequest,
    wrapper: LLMWrapperDep,
    prompt_version: PromptVersionQuery = "v1",
) -> AsyncIterable[ServerSentEvent]:
    """Igual que `/estimate`, pero en streaming con Server-Sent Events.

    Eventos (el `data` siempre es JSON):
    - `token`: trozo de texto de la estimación.
    - `done`: `EstimationResult` (versión del prompt y métricas de la llamada).
    - `error`: `{"detail": ...}` si falla el proveedor. La respuesta ya empezó con 200,
      así que el error viaja como evento, no como código HTTP.
    """
    _bind_request_context(request, prompt_version)
    logger.info("estimation_stream_started", description_chars=len(request.description))
    stream = stream_estimation(request, wrapper, prompt_version)
    try:
        async for text in stream:
            yield ServerSentEvent(event="token", data=text)
    except LLMError as exc:
        yield ServerSentEvent(event="error", data={"detail": str(exc)})
        return

    result = EstimationResult(prompt_version=prompt_version, metadata=_metadata(stream.result))
    logger.info("estimation_stream_finished", cached=result.metadata.cached)
    yield ServerSentEvent(event="done", data=result)
