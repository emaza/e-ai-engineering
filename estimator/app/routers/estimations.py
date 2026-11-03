from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.config import SettingsDep
from app.schemas import EstimationRequest, EstimationResponse
from app.services.llm_service import LLMServiceError, generate_estimation

router = APIRouter(tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(
    request: EstimationRequest,
    settings: SettingsDep,
) -> EstimationResponse:
    """Recibe la transcripción de una reunión y devuelve la estimación generada por el LLM."""
    try:
        result = await generate_estimation(request.transcription, settings)
    except LLMServiceError as exc:
        # El fallo es del proveedor externo, no de nuestro servicio: 502 Bad Gateway
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return EstimationResponse(**result, created_at=datetime.now(UTC))
