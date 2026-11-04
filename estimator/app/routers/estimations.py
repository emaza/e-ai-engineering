from collections.abc import AsyncIterable
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.schemas import EstimationMetadata, EstimationRequest, EstimationResponse
from app.services.llm_service import generate_estimation, stream_estimation
from app.services.llm_wrapper import LLMError, LLMWrapperDep

router = APIRouter(tags=["estimations"])
logger = structlog.get_logger(__name__)


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(
    request: EstimationRequest,
    wrapper: LLMWrapperDep,
) -> EstimationResponse:
    """Recibe la transcripción de una reunión y devuelve la estimación generada por el LLM."""
    try:
        result = await generate_estimation(request.transcription, wrapper)
    except LLMError as exc:
        # El fallo es del proveedor externo, no de nuestro servicio: 502 Bad Gateway
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return EstimationResponse(**result, created_at=datetime.now(UTC))


@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(
    request: EstimationRequest,
    wrapper: LLMWrapperDep,
) -> AsyncIterable[ServerSentEvent]:
    """Igual que `/estimate`, pero en streaming con Server-Sent Events.

    Eventos (el `data` siempre es JSON):
    - `token`: trozo de texto de la estimación.
    - `done`: metadatos de la llamada (modelo, tokens, latencia, coste, caché).
    - `error`: `{"detail": ...}` si falla el proveedor. La respuesta ya empezó con 200,
      así que el error viaja como evento, no como código HTTP.
    """
    logger.info("estimation_stream_started", transcription_chars=len(request.transcription))
    stream = stream_estimation(request.transcription, wrapper)
    try:
        async for text in stream:
            yield ServerSentEvent(event="token", data=text)
    except LLMError as exc:
        yield ServerSentEvent(event="error", data={"detail": str(exc)})
        return

    metadata = EstimationMetadata(**stream.result, created_at=datetime.now(UTC))
    logger.info("estimation_stream_finished", cached=metadata.cached)
    yield ServerSentEvent(event="done", data=metadata)
