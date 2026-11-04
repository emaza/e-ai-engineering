"""Interfaz conversacional del estimador: `uv run streamlit run streamlit_app.py`.

Reutiliza la lógica del servicio FastAPI (mismo system prompt CAG, mismos clientes y la
misma configuración leída de .env); no llama al endpoint HTTP.
"""

import asyncio
import threading
from collections.abc import AsyncIterable, Iterator

import streamlit as st
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.context.examples import ESTIMATION_EXAMPLES
from app.services.llm_service import EstimationStream, LLMServiceError, build_system_prompt

st.set_page_config(page_title="Estimador CAG", page_icon="📐", layout="wide")


@st.cache_resource
def _event_loop() -> asyncio.AbstractEventLoop:
    """Event loop único en un hilo de fondo.

    Streamlit ejecuta el script de forma síncrona y los clientes del servicio son async y
    están cacheados: si cada mensaje usara `asyncio.run` (un loop nuevo), el cliente
    cacheado quedaría atado a un loop ya cerrado.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="llm-event-loop").start()
    return loop


def iterate_sync(stream: AsyncIterable[str]) -> Iterator[str]:
    """Convierte el iterable async en un generador síncrono para `st.write_stream`."""
    loop = _event_loop()
    iterator = aiter(stream)
    while True:
        try:
            yield asyncio.run_coroutine_threadsafe(anext(iterator), loop).result()
        except StopAsyncIteration:
            return


def render_sidebar(settings: Settings) -> None:
    """Nivel 3: qué contexto usa el modelo y métricas de la última llamada."""
    with st.sidebar:
        st.header("Contexto CAG")
        st.caption(f"Proveedor: **{settings.LLM_PROVIDER}** · Modelo: **{settings.model}**")

        if st.button("Nueva conversación", icon="🗑️", width="stretch"):
            st.session_state.messages = []
            st.session_state.last_result = None
            st.rerun()

        st.subheader("Última llamada")
        result = st.session_state.last_result
        if result is None:
            st.caption("Aún no se ha generado ninguna estimación.")
        else:
            usage = result["usage"]
            # El nombre completo del modelo no cabe en un st.metric
            st.markdown(f"**Modelo:** `{result['model']}`")
            col_in, col_out = st.columns(2)
            col_in.metric("Tokens entrada", f"{usage['input_tokens']:,}".replace(",", "."))
            col_out.metric("Tokens salida", f"{usage['output_tokens']:,}".replace(",", "."))
            col_time, col_cost = st.columns(2)
            col_time.metric("Tiempo", f"{result['latency_ms'] / 1000:.1f} s")
            cost = result["estimated_cost_usd"]
            col_cost.metric("Coste", "—" if cost is None else f"${cost:.4f}")
            if result["finish_reason"] == "length":
                st.warning("La respuesta se cortó al llegar al límite de tokens.")

        st.subheader("System prompt")
        with st.expander("Ver system prompt activo"):
            st.code(build_system_prompt(), language="markdown", wrap_lines=True)

        st.subheader("Estimaciones de referencia")
        for i, example in enumerate(ESTIMATION_EXAMPLES, start=1):
            title = example["estimation"].splitlines()[0].removeprefix("## Estimación: ")
            with st.expander(f"Ejemplo {i}: {title}"):
                st.markdown(f"**Resumen de la reunión:** {example['meeting_summary']}")
                st.markdown(example["estimation"])


st.title("📐 Estimador de proyectos de software")
st.caption(
    "Pega la transcripción de una reunión con el cliente y recibirás una estimación. "
    "Después puedes pedir ajustes en el mismo chat."
)

try:
    settings = get_settings()
except ValidationError as exc:
    st.error(f"Configuración incompleta. Revisa el archivo `.env`:\n\n{exc}")
    st.stop()

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
        # Se envía el historial completo: los turnos anteriores dan contexto a los ajustes
        stream = EstimationStream(list(st.session_state.messages), settings)
        try:
            estimation = st.write_stream(iterate_sync(stream))
        except LLMServiceError as exc:
            # Sin respuesta, se retira la pregunta para no dejar dos turnos de usuario seguidos
            st.session_state.messages.pop()
            st.error(f"No se pudo generar la estimación. {exc}")
        else:
            st.session_state.messages.append({"role": "assistant", "content": estimation})
            st.session_state.last_result = stream.result

render_sidebar(settings)
