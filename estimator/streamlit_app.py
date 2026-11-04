"""Cliente web del estimador: `uv run streamlit run streamlit_app.py`.

Es un cliente HTTP puro de la API (UI → HTTP/SSE → FastAPI → LLM): no importa nada de
`app/` ni conoce ninguna API key. La comunicación con la API está en `estimator_client.py`;
aquí solo está la interfaz.

El formulario produce un EstimationRequest tipado: el usuario elige parámetros y el
servicio construye el prompt con sus templates.
"""

from typing import Any

import pandas as pd
import streamlit as st

from estimator_client import (
    API_URL,
    DESCRIPTION_MAX,
    DESCRIPTION_MIN,
    DETAIL_LEVELS,
    OUTPUT_FORMATS,
    PROJECT_TYPES,
    PROMPT_VERSIONS,
    EstimatorAPIError,
    describe_params,
    reference_projects,
    stream_estimation,
)


def render_sidebar() -> None:
    with st.sidebar:
        st.header("Servicio")
        st.caption(f"API: `{API_URL}`")
        if st.button("Limpiar historial", icon="🗑️", width="stretch"):
            st.session_state.history = []
            st.rerun()

        st.subheader("Última llamada")
        if not st.session_state.history:
            st.caption("Aún no se ha generado ninguna estimación.")
            return

        last = st.session_state.history[-1]["result"]
        meta, usage = last["metadata"], last["metadata"]["usage"]
        st.markdown(f"**Prompt:** `{last['prompt_version']}`  \n**Modelo:** `{meta['model']}`")
        if meta["cached"]:
            st.success("Respuesta servida desde la caché", icon="⚡")
        col_in, col_out = st.columns(2)
        col_in.metric("Tokens entrada", f"{usage['input_tokens']:,}".replace(",", "."))
        col_out.metric("Tokens salida", f"{usage['output_tokens']:,}".replace(",", "."))
        col_time, col_cost = st.columns(2)
        col_time.metric("Tiempo", f"{meta['latency_ms'] / 1000:.1f} s")
        cost = meta["estimated_cost_usd"]
        col_cost.metric("Coste", "—" if cost is None else f"${cost:.4f}")
        if meta["finish_reason"] == "length":
            st.warning("La respuesta se cortó al llegar al límite de tokens.")


st.set_page_config(page_title="Estimador", page_icon="📐", layout="wide")
st.title("📐 Estimador de proyectos de software")
st.caption("Describe el proyecto, elige cómo quieres la estimación y genera.")

if "history" not in st.session_state:
    st.session_state.history = []

with st.form("estimation_form"):
    description = st.text_area(
        "Descripción del proyecto",
        max_chars=DESCRIPTION_MAX,
        height=160,
        placeholder="Qué necesita el cliente, para quién, integraciones, plazos…",
    )
    col_left, col_right = st.columns(2)
    project_type = col_left.selectbox(
        "Tipo de proyecto", list(PROJECT_TYPES), format_func=PROJECT_TYPES.__getitem__
    )
    output_format = col_right.selectbox(
        "Formato de salida", list(OUTPUT_FORMATS), format_func=OUTPUT_FORMATS.__getitem__
    )
    detail_level = col_left.radio(
        "Nivel de detalle",
        list(DETAIL_LEVELS),
        index=1,
        format_func=DETAIL_LEVELS.__getitem__,
        horizontal=True,
    )
    prompt_version = col_right.selectbox(
        "Versión del prompt", list(PROMPT_VERSIONS), format_func=PROMPT_VERSIONS.__getitem__
    )
    with st.expander("Proyectos de referencia (opcional)"):
        st.caption("Proyectos parecidos ya entregados, con sus horas reales. Máximo 5.")
        references_table = st.data_editor(
            pd.DataFrame(
                {
                    "name": pd.Series(dtype="string"),
                    "description": pd.Series(dtype="string"),
                    "actual_hours": pd.Series(dtype="Int64"),
                }
            ),
            num_rows="dynamic",
            width="stretch",
            column_config={
                "name": st.column_config.TextColumn("Nombre", max_chars=120),
                "description": st.column_config.TextColumn("Descripción", max_chars=500),
                "actual_hours": st.column_config.NumberColumn("Horas reales", min_value=1, step=1),
            },
            key="reference_projects",
        )
    submitted = st.form_submit_button("Generar estimación", type="primary")

shown_latest = False
if submitted:
    payload = {
        "description": description.strip(),
        "project_type": project_type,
        "detail_level": detail_level,
        "output_format": output_format,
        "reference_projects": reference_projects(references_table),
    }
    if len(payload["description"]) < DESCRIPTION_MIN:
        st.warning(f"La descripción debe tener al menos {DESCRIPTION_MIN} caracteres.")
    else:
        st.subheader("Estimación")
        st.caption(describe_params(payload, prompt_version))
        result: dict[str, Any] = {}
        try:
            text = st.write_stream(stream_estimation(payload, prompt_version, result))
        except EstimatorAPIError as exc:
            st.error(str(exc))
        else:
            st.session_state.history.append(
                {
                    "payload": payload,
                    "prompt_version": prompt_version,
                    "text": text,
                    "result": result,
                }
            )
            shown_latest = True
elif st.session_state.history:
    latest = st.session_state.history[-1]
    st.subheader("Estimación")
    st.caption(describe_params(latest["payload"], latest["prompt_version"]))
    st.markdown(latest["text"])
    shown_latest = True

previous = st.session_state.history[:-1] if shown_latest else st.session_state.history
if previous:
    st.subheader("Estimaciones anteriores")
    for item in reversed(previous):
        with st.expander(describe_params(item["payload"], item["prompt_version"])):
            st.markdown(item["text"])

render_sidebar()
