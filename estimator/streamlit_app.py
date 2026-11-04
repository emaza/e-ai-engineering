"""Cliente web del estimador: `uv run streamlit run streamlit_app.py`.

Es un cliente HTTP puro de la API (UI → HTTP/SSE → FastAPI → LLM): no importa nada de
`app/` ni conoce ninguna API key. Solo necesita la URL del servicio, que se lee de
ESTIMATOR_API_URL (por defecto, el uvicorn local).
"""

import json
import os
from collections.abc import Iterable, Iterator
from typing import Any

import httpx
import streamlit as st

API_URL = os.environ.get("ESTIMATOR_API_URL", "http://localhost:8000").rstrip("/")
STREAM_ENDPOINT = f"{API_URL}/api/v1/estimate/stream"
# Generar una estimación tarda; solo la conexión debe fallar rápido
TIMEOUT = httpx.Timeout(120.0, connect=5.0)


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


def stream_estimation(transcription: str, metadata: dict[str, Any]) -> Iterator[str]:
    """Pide la estimación a la API y va dando el texto según llega.

    Al terminar, `metadata` contiene el evento `done` (modelo, tokens, latencia, coste).
    """
    try:
        with httpx.stream(
            "POST", STREAM_ENDPOINT, json={"transcription": transcription}, timeout=TIMEOUT
        ) as response:
            if response.status_code != 200:
                response.read()
                raise EstimatorAPIError(_describe_http_error(response))
            for event, data in parse_sse(response.iter_lines()):
                if event == "token":
                    yield data
                elif event == "done":
                    metadata.update(data)
                elif event == "error":
                    raise EstimatorAPIError(data["detail"])
    except httpx.ConnectError as exc:
        raise EstimatorAPIError(
            f"No se puede conectar con la API en {API_URL}. ¿Está arrancada? "
            "(`uv run uvicorn app.main:app`)"
        ) from exc
    except httpx.HTTPError as exc:
        raise EstimatorAPIError(f"Error de comunicación con la API: {exc}") from exc

    if not metadata:
        raise EstimatorAPIError("La respuesta de la API se cortó antes de terminar.")


def _describe_http_error(response: httpx.Response) -> str:
    try:
        detail = response.json()["detail"]
    except (ValueError, KeyError):
        return f"La API respondió {response.status_code}: {response.text}"
    if isinstance(detail, list):  # 422: errores de validación de Pydantic
        detail = "; ".join(error["msg"] for error in detail)
    return f"La API respondió {response.status_code}: {detail}"


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Servicio")
        st.caption(f"API: `{API_URL}`")
        if st.button("Nueva conversación", icon="🗑️", width="stretch"):
            st.session_state.messages = []
            st.session_state.last_result = None
            st.rerun()

        st.subheader("Última llamada")
        result = st.session_state.last_result
        if result is None:
            st.caption("Aún no se ha generado ninguna estimación.")
            return

        usage = result["usage"]
        st.markdown(f"**Modelo:** `{result['model']}`")
        if result["cached"]:
            st.success("Respuesta servida desde la caché", icon="⚡")
        col_in, col_out = st.columns(2)
        col_in.metric("Tokens entrada", f"{usage['input_tokens']:,}".replace(",", "."))
        col_out.metric("Tokens salida", f"{usage['output_tokens']:,}".replace(",", "."))
        col_time, col_cost = st.columns(2)
        col_time.metric("Tiempo", f"{result['latency_ms'] / 1000:.1f} s")
        cost = result["estimated_cost_usd"]
        col_cost.metric("Coste", "—" if cost is None else f"${cost:.4f}")
        if result["finish_reason"] == "length":
            st.warning("La respuesta se cortó al llegar al límite de tokens.")


st.set_page_config(page_title="Estimador", page_icon="📐", layout="wide")
st.title("📐 Estimador de proyectos de software")
st.caption(
    "Pega la transcripción de una reunión con el cliente y recibirás una estimación. "
    "Cada mensaje es una estimación nueva."
)

if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.last_result = None

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Pega aquí la transcripción de la reunión…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        metadata: dict[str, Any] = {}
        try:
            estimation = st.write_stream(stream_estimation(prompt, metadata))
        except EstimatorAPIError as exc:
            # Sin estimación, la pregunta tampoco se queda en el historial
            st.session_state.messages.pop()
            st.error(str(exc))
        else:
            st.session_state.messages.append({"role": "assistant", "content": estimation})
            st.session_state.last_result = metadata

render_sidebar()
