"""Carga y renderiza los prompts versionados (templates Jinja2).

Cada prompt vive en `app/prompts/<tarea>/<versión>/`. Cambiar de versión es cambiar un
argumento: el endpoint, el servicio y el wrapper no se tocan.

    system, user = render_estimation_prompt(request, version="v2")
"""

import hashlib
from functools import lru_cache
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas import EstimationRequest

logger = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent

# Tarifas de referencia (EUR/hora). Son datos de negocio, no texto del prompt: los
# templates las reciben como variable y calculan los costes de los ejemplos con ellas.
HOURLY_RATES: dict[str, float] = {"dev": 62.50, "design": 50.00}


@lru_cache
def get_environment() -> Environment:
    """Entorno Jinja2 compartido.

    - StrictUndefined: una variable que falta en el contexto es un error, no un texto vacío.
    - trim_blocks / lstrip_blocks: las etiquetas {% %} no dejan líneas ni espacios sueltos.
    """
    env = Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=False,  # el destino es un LLM, no HTML
    )
    env.filters["eur"] = format_eur
    return env


def format_eur(amount: float) -> str:
    """Importe en euros con formato español: 1.234,50 €."""
    return f"{amount:,.2f} €".replace(",", "_").replace(".", ",").replace("_", ".")


def available_versions(task: str = "estimation") -> list[str]:
    return sorted(p.name for p in (PROMPTS_DIR / task).iterdir() if p.is_dir())


def render_estimation_prompt(request: EstimationRequest, version: str = "v1") -> tuple[str, str]:
    """Devuelve (system, user) listos para enviar al modelo como mensajes separados."""
    if version not in available_versions():
        raise ValueError(f"Versión de prompt desconocida: {version!r}")

    env = get_environment()
    # mode="json": los enums pasan a su valor en string ("phases_table")
    context = request.model_dump(mode="json") | {"rates": HOURLY_RATES}
    system = env.get_template(f"estimation/{version}/system.j2").render(context).strip()
    user = env.get_template(f"estimation/{version}/user.j2").render(context).strip()

    logger.info(
        "prompt_rendered",
        prompt_version=version,
        system_sha256=_short_hash(system),
        user_sha256=_short_hash(user),
        system_chars=len(system),
        user_chars=len(user),
    )
    return system, user


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
