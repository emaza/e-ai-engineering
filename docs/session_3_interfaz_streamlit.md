# Interfaz conversacional con Streamlit (ejercicio de la sesión 3)

> **Rama:** `session_3`, creada a partir de `session_2`.
> **Archivos tocados** (en `estimator/`):
> - `streamlit_app.py` (nuevo)
> - `app/services/llm_service.py`
> - `tests/test_streamlit_app.py` (nuevo), `tests/test_llm_service.py`, `tests/test_structure.py`
> - `pyproject.toml`, `uv.lock`, `Dockerfile`, `README.md`

El objetivo del ejercicio es añadir al Proyecto 1 una interfaz web de chat. Se pega la transcripción de una reunión y la estimación aparece en streaming, sin usar curl, Postman ni Swagger. Están hechos los tres niveles:

| Nivel | Requisito | Cómo se resuelve |
|---|---|---|
| 1 — Chat básico | Chat con historial visible; mismo system prompt que el endpoint CAG; API key fuera del código | `st.chat_message` + `st.chat_input`, historial en `st.session_state.messages`, `build_system_prompt()` del servicio y key leída de `.env` con `get_settings()` |
| 2 — Streaming | La estimación se escribe token a token | `st.write_stream` sobre la nueva clase `EstimationStream` |
| 3 — Contexto CAG (opcional) | Panel lateral con system prompt, ejemplos y métricas | `st.sidebar` con el prompt, los ejemplos de `ESTIMATION_EXAMPLES` y las métricas de la última llamada |

Para arrancarla:

```bash
cd estimator             # desde aquí, para que encuentre el .env
uv sync
uv run streamlit run streamlit_app.py    # http://localhost:8501
```

---

## 1. Decisiones de diseño

### 1.1 Se envía el historial completo al LLM

Cada mensaje envía al LLM toda la conversación (turnos `user` y `assistant`), no solo el último mensaje. Así se pueden pedir ajustes sobre la estimación anterior, por ejemplo "quita el diseñador y recalcula".

- **Coste:** los tokens de entrada crecen con cada turno.
- **El endpoint no cambia:** `POST /api/v1/estimate` sigue enviando un único mensaje.

### 1.2 Se reutiliza la lógica del servicio, sin pasar por HTTP

La app importa `app.services.llm_service` directamente y no llama al endpoint. Usa el mismo system prompt CAG, los mismos clientes cacheados y la misma configuración. No hace falta tener uvicorn arrancado.

### 1.3 Un event loop en un hilo de fondo

Los clientes del servicio son async y están cacheados con `lru_cache`. Streamlit, en cambio, ejecuta el script de forma síncrona.

Con `asyncio.run` en cada mensaje se crearía un event loop nuevo cada vez. El cliente cacheado quedaría atado al primer loop, que ya estaría cerrado, y las llamadas fallarían a partir del segundo mensaje.

La solución tiene dos piezas:
- `_event_loop()`: crea un único loop que corre en un hilo daemon y se cachea con `@st.cache_resource`.
- `iterate_sync()`: convierte el iterable async en un generador síncrono, avanzándolo con `asyncio.run_coroutine_threadsafe`. Ese generador es el que recibe `st.write_stream`.

### 1.4 Streamlit fuera de la imagen Docker

Streamlit está en un grupo de dependencias aparte, `ui`:
- `pyproject.toml` incluye `[tool.uv] default-groups = ["dev", "ui"]`, así que `uv sync` y la CI lo instalan.
- El `Dockerfile` usa `uv sync --locked --no-default-groups`, así que la imagen de la API sigue sin Streamlit. Está comprobado con `find_spec("streamlit")` dentro del contenedor.

---

## 2. Cambios en el servicio (`app/services/llm_service.py`)

- **Llamadas con lista de mensajes:** `_call_openai` / `_call_anthropic` reciben una lista de mensajes en vez de la transcripción. `generate_estimation` envuelve la transcripción en `[{"role": "user", ...}]`, así el endpoint y sus tests no cambian.
- **`_finalize(result, start)`:** helper común que añade latencia y coste, avisa si la respuesta se cortó y registra la llamada. Lo usan el modo normal y el modo streaming.
- **`_openai_meta` / `_anthropic_meta`:** extraen modelo, tokens y `finish_reason`. Se comparten entre los dos modos.
- **Nueva clase `EstimationStream(messages, settings)`:**
  - al iterarla da los trozos de texto según llegan;
  - al terminar, `.result` contiene el mismo dict que `generate_estimation`: estimation, model, provider, usage, finish_reason, latency_ms y estimated_cost_usd.

### Streaming en cada SDK

| | OpenAI (Responses API) | Anthropic (Messages API) |
|---|---|---|
| Llamada | `responses.create(..., stream=True)` | `async with client.messages.stream(...)` |
| Texto | eventos `response.output_text.delta` → `event.delta` | `stream.text_stream` |
| Tokens y modelo | evento final `response.completed` / `response.incomplete` → `event.response` | `await stream.get_final_message()` |
| Errores | `openai.APIError`, o eventos `response.failed` / `error` | `anthropic.APIError` |

Todos los errores se convierten en `LLMServiceError`, igual que en el modo normal.

---

## 3. La interfaz (`streamlit_app.py`)

- **Chat:** el historial se repinta en cada ejecución a partir de `st.session_state.messages`. La respuesta se guarda en el historial cuando `st.write_stream` termina y devuelve el texto completo.
- **Errores del proveedor:** se muestran con `st.error` y se retira el mensaje del usuario del historial, para que no queden dos turnos de usuario seguidos.
- **Configuración incompleta** (falta la key): se muestra el error de validación y `st.stop()`.
- **Panel lateral:** se pinta al final del script, para que las métricas sean las de la llamada recién hecha. Contiene:
  - proveedor y modelo activos;
  - botón "Nueva conversación";
  - métricas de la última llamada: modelo, tokens de entrada y salida, tiempo y coste estimado, y un aviso si la respuesta se cortó;
  - system prompt en solo lectura (`st.code`);
  - un expander por cada estimación de referencia.

---

## 4. Tests

Los tests no llaman al LLM: usan clientes y streams falsos. En total pasan 53, 9 de ellos nuevos.

- **`test_llm_service.py`** (streaming, para los dos proveedores):
  - los trozos concatenados forman el texto completo;
  - `.result` trae uso de tokens, coste y latencia;
  - el historial llega como `input` / `messages`;
  - el truncado se refleja en `finish_reason = "length"`;
  - los errores se convierten en `LLMServiceError`.
- **`test_streamlit_app.py`** (con `streamlit.testing.v1.AppTest`):
  - render inicial;
  - chat con streaming y métricas;
  - un segundo mensaje envía el historial completo;
  - un error no se queda en el historial;
  - el botón de nueva conversación vacía el historial.
- **`test_structure.py`:** exige que exista `streamlit_app.py`.

---

## 5. Verificación con llamadas reales

| Prueba | Resultado |
|---|---|
| Chrome, OpenAI `gpt-4o-mini`: transcripción de ejemplo | Estimación en streaming: 130 h, 8.125 €. Métricas: 1.248 tokens de entrada, 342 de salida, 8,3 s, $0,0004 |
| Ajuste: "Quita la tarea de SEO y recalcula los totales" | El modelo usa la estimación anterior: 120 h, 7.500 € |
| Script, Anthropic `claude-haiku-4-5` (sin la interfaz) | 301 trozos, primer token en 1,1 s; 1.618 tokens de entrada y 789 de salida; 8,4 s; $0,0056 |
| `docker build` | La imagen se construye y no incluye Streamlit |

---

## 6. Checklist del enunciado

- [x] `streamlit run streamlit_app.py` abre una interfaz de chat en el navegador
- [x] Se pega una transcripción y se recibe una estimación de software
- [x] La conversación persiste en pantalla y admite varias preguntas seguidas
- [x] La respuesta se muestra en streaming, no de golpe
- [x] La API key se lee desde `.env`, no está en el código
- [x] Nivel 3: panel lateral con system prompt, contexto CAG y métricas
