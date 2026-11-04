import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response

from app.config import SettingsDep, get_settings
from app.logging_config import configure_logging
from app.routers import estimations

VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque: configura el logging y valida la configuración (falla pronto si falta la key)."""
    settings = get_settings()
    configure_logging(settings)
    structlog.get_logger(__name__).info(
        "estimator_started",
        environment=settings.APP_ENV,
        provider=settings.LLM_PROVIDER,
        model=settings.model,
        cache_enabled=settings.CACHE_ENABLED,
    )
    yield


app = FastAPI(
    title="Estimador CAG",
    description=(
        "Servicio de estimación de proyectos de software con IA. Recibe la descripción de "
        "un proyecto y parámetros tipados (tipo, nivel de detalle, formato de salida) y "
        "devuelve una estimación generada por un LLM. El prompt sale de templates Jinja2 "
        "versionados (`?prompt_version=v1|v2`) y lleva estimaciones de referencia como "
        "contexto estático (**CAG**, Cache Augmented Generation)."
    ),
    version=VERSION,
    lifespan=lifespan,
)

app.include_router(estimations.router, prefix="/api/v1")


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Asigna un request_id (o reutiliza el del cliente) y lo añade a todos los logs del
    request. También se devuelve en la cabecera X-Request-ID para poder correlacionar."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id, method=request.method, path=request.url.path
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


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
