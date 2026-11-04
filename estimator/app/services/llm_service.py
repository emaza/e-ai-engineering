"""Lógica de negocio: convierte la petición tipada en prompt y la envía al LLM.

El prompt sale de los templates versionados (`app/prompts/`); lo que depende del
proveedor (SDKs, streaming, caché, coste) vive en `llm_wrapper.py`. Mensajes:
    [system] → instrucciones + formato + nivel de detalle + ejemplos (system.j2)
    [user]   → tipo y descripción del proyecto + proyectos de referencia (user.j2)
"""

from typing import Any

from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, PromptVersion
from app.services.llm_wrapper import LLMStream, LLMWrapper


def _prompt(request: EstimationRequest, version: PromptVersion) -> tuple[str, list[dict]]:
    """System y user como mensajes separados, no concatenados en uno."""
    system, user = render_estimation_prompt(request, version)
    return system, [{"role": "user", "content": user}]


async def generate_estimation(
    request: EstimationRequest, wrapper: LLMWrapper, version: PromptVersion = "v1"
) -> dict[str, Any]:
    """Estimación completa: el dict de `LLMWrapper.complete()`."""
    return await wrapper.complete(*_prompt(request, version))


def stream_estimation(
    request: EstimationRequest, wrapper: LLMWrapper, version: PromptVersion = "v1"
) -> LLMStream:
    """Estimación en streaming: iterable async de trozos de texto con `.result` al final."""
    return wrapper.stream(*_prompt(request, version))
