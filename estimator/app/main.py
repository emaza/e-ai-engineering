import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import SettingsDep, get_settings
from app.routers import estimations

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque: configura el logging y valida la configuración (falla pronto si falta la key)."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger(__name__).info(
        "Estimador arrancado (%s) con %s/%s",
        settings.APP_ENV,
        settings.LLM_PROVIDER,
        settings.model,
    )
    yield


app = FastAPI(
    title="Estimador CAG",
    description=(
        "Servicio de estimación de proyectos de software con IA. Recibe la transcripción de "
        "una reunión con el cliente y devuelve una estimación generada por un LLM con "
        "arquitectura **CAG** (Cache Augmented Generation): las estimaciones de referencia "
        "viajan como contexto estático dentro del prompt."
    ),
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(estimations.router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health(settings: SettingsDep) -> dict[str, str]:
    """Estado del servicio."""
    return {
        "status": "healthy",
        "version": VERSION,
        "environment": settings.APP_ENV,
        "provider": settings.LLM_PROVIDER,
        "model": settings.model,
    }
