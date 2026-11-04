"""Logging estructurado con structlog.

Cada evento es un diccionario (nombre del evento + campos), no una frase. En desarrollo se
pinta legible en consola; en staging/production sale en JSON, listo para un agregador.
Los logs de librerías que usan el logging estándar (uvicorn) pasan por el mismo formato.
"""

import logging

import structlog

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,  # request_id y demás contexto del request
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.APP_ENV == "development"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL)

    # uvicorn trae sus propios handlers: los quitamos para que todo salga por el nuestro
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Los SDKs y su cliente HTTP son muy verbosos en DEBUG; cada llamada ya queda
    # registrada en nuestro evento `llm_call_completed`
    for name in ("openai", "anthropic", "httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
