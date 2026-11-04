"""Lógica de negocio: construye el prompt CAG de estimación y lo envía al LLM.

Lo que depende del proveedor (SDKs, streaming, caché, coste) vive en `llm_wrapper.py`;
aquí solo está el dominio. Patrón de mensajes:
    [system]    → instrucciones + ejemplos de estimaciones previas (contexto CAG)
    [user]      → transcripción de la reunión a estimar
    [assistant] → estimación generada por el modelo
"""

from typing import Any

from app.context.examples import HOURLY_RATES, format_examples_for_prompt
from app.services.llm_wrapper import LLMStream, LLMWrapper


def build_system_prompt() -> str:
    """System prompt: rol + formato de salida + ejemplos inyectados (CAG)."""
    return f"""Eres un consultor senior de proyectos de software con más de 15 años de experiencia en estimación.
Tu tarea es generar la estimación de un nuevo proyecto a partir de la transcripción de una reunión con el cliente.

Usa como referencia las estimaciones de proyectos anteriores que aparecen más abajo: imita su estructura, su nivel de detalle y sus órdenes de magnitud, adaptándolos al proyecto descrito.

Formato obligatorio de la respuesta (Markdown):
- Título "## Estimación: <nombre del proyecto>"
- Tabla de desglose de tareas con las columnas: Tarea | Perfil | Horas | Coste
- Total estimado en horas y coste total (deben coincidir con la suma de la tabla)
- Equipo recomendado
- Duración estimada en semanas
- Supuestos realizados y riesgos principales
- Preguntas abiertas para el cliente si falta información relevante (no la inventes)

Tarifas: {HOURLY_RATES["dev"]:.2f} €/h para desarrollo y {HOURLY_RATES["design"]:.2f} €/h para diseño.

Responde en español.

## Estimaciones de referencia

{format_examples_for_prompt()}"""


def _messages(transcription: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": transcription}]


async def generate_estimation(transcription: str, wrapper: LLMWrapper) -> dict[str, Any]:
    """Estimación completa: el dict de `LLMWrapper.complete()`."""
    return await wrapper.complete(build_system_prompt(), _messages(transcription))


def stream_estimation(transcription: str, wrapper: LLMWrapper) -> LLMStream:
    """Estimación en streaming: iterable async de trozos de texto con `.result` al final."""
    return wrapper.stream(build_system_prompt(), _messages(transcription))
