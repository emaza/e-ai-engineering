"""Cliente HTTP/SSE del servicio de estimación, sin dependencias de Streamlit.

Lo usa `streamlit_app.py`, pero sirve para cualquier cliente Python. No importa nada de
`app/` ni conoce API keys: solo necesita la URL del servicio (ESTIMATOR_API_URL).

Las claves de los diccionarios de opciones son los valores exactos de los enums de la API
("mobile_app", no "MOBILE_APP"); los valores son las etiquetas que ve el usuario.
"""

import json
import os
from collections.abc import Iterable, Iterator
from typing import Any

import httpx
import pandas as pd

API_URL = os.environ.get("ESTIMATOR_API_URL", "http://localhost:8000").rstrip("/")
STREAM_ENDPOINT = f"{API_URL}/api/v1/estimate/stream"
# Generar una estimación tarda; solo la conexión debe fallar rápido
TIMEOUT = httpx.Timeout(120.0, connect=5.0)

DESCRIPTION_MIN, DESCRIPTION_MAX = 20, 2000

PROJECT_TYPES = {
    "mobile_app": "App móvil",
    "web_saas": "SaaS web",
    "internal_tool": "Herramienta interna",
    "data_pipeline": "Pipeline de datos",
}
DETAIL_LEVELS = {"summary": "Resumen", "medium": "Medio", "detailed": "Detallado"}
OUTPUT_FORMATS = {
    "phases_table": "Tabla por fases",
    "line_items": "Partidas",
    "narrative": "Narrativo",
}
PROMPT_VERSIONS = {
    "v1": "v1 · estándar",
    "v2": "v2 · conservador (rangos y contingencia)",
}
REFERENCE_COLUMNS = ["name", "description", "actual_hours"]


class EstimatorAPIError(Exception):
    """La API no está disponible o devolvió un error."""


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, Any]]:
    """Convierte las líneas de un stream SSE en pares (event, data).

    Un evento son líneas `event:` / `data:` terminadas por una línea en blanco. Las que
    empiezan por `:` son comentarios (keep-alive) y se ignoran. Nuestra API envía el
    `data` siempre en JSON.
    """
    event, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)


def stream_estimation(
    payload: dict[str, Any], prompt_version: str, result: dict[str, Any]
) -> Iterator[str]:
    """Pide la estimación a la API y va dando el texto según llega.

    Al terminar, `result` contiene el evento `done`: prompt_version y metadata.
    """
    try:
        with httpx.stream(
            "POST",
            STREAM_ENDPOINT,
            json=payload,
            params={"prompt_version": prompt_version},
            timeout=TIMEOUT,
        ) as response:
            if response.status_code != 200:
                response.read()
                raise EstimatorAPIError(_describe_http_error(response))
            for event, data in parse_sse(response.iter_lines()):
                if event == "token":
                    yield data
                elif event == "done":
                    result.update(data)
                elif event == "error":
                    raise EstimatorAPIError(data["detail"])
    except httpx.ConnectError as exc:
        raise EstimatorAPIError(
            f"No se puede conectar con la API en {API_URL}. ¿Está arrancada? "
            "(`uv run uvicorn app.main:app`)"
        ) from exc
    except httpx.HTTPError as exc:
        raise EstimatorAPIError(f"Error de comunicación con la API: {exc}") from exc

    if not result:
        raise EstimatorAPIError("La respuesta de la API se cortó antes de terminar.")


def _describe_http_error(response: httpx.Response) -> str:
    try:
        detail = response.json()["detail"]
    except (ValueError, KeyError):
        return f"La API respondió {response.status_code}: {response.text}"
    if isinstance(detail, list):  # 422: errores de validación de Pydantic
        detail = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'][1:])}: {error['msg']}" for error in detail
        )
    return f"La API respondió {response.status_code}: {detail}"


def reference_projects(table: pd.DataFrame) -> list[dict[str, Any]] | None:
    """Filas completas del editor de proyectos de referencia; None si no hay ninguna."""
    projects = [
        {
            "name": str(row["name"]).strip(),
            "description": str(row["description"]).strip(),
            "actual_hours": int(row["actual_hours"]),
        }
        for row in table.to_dict("records")
        if all(pd.notna(row[col]) and str(row[col]).strip() for col in REFERENCE_COLUMNS)
    ]
    return projects or None


def describe_params(payload: dict[str, Any], prompt_version: str) -> str:
    refs = len(payload.get("reference_projects") or [])
    return " · ".join(
        [
            PROJECT_TYPES[payload["project_type"]],
            DETAIL_LEVELS[payload["detail_level"]],
            OUTPUT_FORMATS[payload["output_format"]],
            f"prompt {prompt_version}",
            *([f"{refs} referencia(s)"] if refs else []),
        ]
    )
