# Sesión 3: UI por HTTP/SSE, wrapper de proveedores, caché y structlog

> **Rama:** `session_3`, creada a partir de `session_2`.
> **Objetivo:** dejar el estimador en el punto de partida que da por hecho el ejercicio de la sesión 4:
> - wrapper de proveedores;
> - cliente Streamlit;
> - caché exact-match, streaming y structlog.
>
> **No incluye** lo del ejercicio 3 que la sesión 4 elimina: el historial de la conversación enviado al LLM, y el system prompt y los ejemplos en el panel lateral.
>
> La primera versión del ejercicio, con Streamlit importando el servicio directamente, está en la rama `session_3_streamlit`, con su documento `docs/session_3_interfaz_streamlit.md`.

## Arquitectura

```
Streamlit (UI) ──HTTP/SSE──▶ FastAPI ──▶ llm_service ──▶ LLMWrapper ──▶ OpenAI / Anthropic
 sin API keys                 routers     (prompt CAG)    caché + logs
```

| Pieza | Archivo | Responsabilidad |
|---|---|---|
| Cliente | `streamlit_app.py` | Chat; envía la transcripción y pinta los tokens según llegan |
| Endpoints | `app/routers/estimations.py` | `POST /estimate` (JSON) y `POST /estimate/stream` (SSE) |
| Dominio | `app/services/llm_service.py` | Qué se le pide al LLM: system prompt CAG + transcripción |
| Wrapper | `app/services/llm_wrapper.py` | Cómo se le pide: SDKs, streaming, caché, coste, logs |
| Caché | `app/services/cache.py` | Exact-match en memoria, LRU + TTL |
| Logs | `app/logging_config.py` + middleware en `app/main.py` | structlog y `request_id` |

---

## 1. Por qué la UI llama a la API por HTTP

En la primera versión, Streamlit importaba el servicio. Funcionaba, pero tenía tres problemas:
- la UI necesitaba la API key;
- había dos puntos de entrada al LLM;
- lo que se añadiera a la API (caché, logs, límites de uso) no se aplicaba a la UI.

Ahora todo pasa por la API:

- **Sin secretos en el cliente.** `streamlit_app.py` no importa `app/`, no lee `.env` y no conoce ninguna key. Solo necesita `ESTIMATOR_API_URL`. Hay un test que lo comprueba.
- **Un único camino al LLM.** La caché y los logs se aplican igual a la UI, a curl o a cualquier otro cliente.
- **Despliegue independiente.** La imagen Docker de la API no lleva Streamlit (grupo de dependencias `ui` + `--no-default-groups`).

### El protocolo SSE

`POST /api/v1/estimate/stream` usa el soporte nativo de SSE de FastAPI 0.162 (`fastapi.sse.EventSourceResponse` y `ServerSentEvent`), sin dependencias extra.

| Evento | `data` (JSON) |
|---|---|
| `token` | Trozo de texto |
| `done` | `EstimationMetadata`: modelo, tokens, latencia, coste, `cached` |
| `error` | `{"detail": ...}` |

Dos decisiones del protocolo:
- **El `data` siempre es JSON**, incluso en los tokens. Un trozo puede contener saltos de línea (`"\n\n"`), y en SSE un salto de línea dentro de `data` obliga a partirlo en varias líneas `data:`. Al serializarlo en JSON, `\n` viaja escapado y cada evento ocupa una sola línea.
- **Los errores son un evento, no un código HTTP.** Cuando el proveedor falla a mitad del stream, la respuesta ya se envió con 200.

En la UI, `parse_sse()` (unas 15 líneas) lee `event:` / `data:`, cierra cada evento con la línea en blanco e ignora los comentarios `:` de keep-alive. `stream_estimation()` es un generador que alimenta `st.write_stream` y guarda el evento `done` para el panel lateral.

---

## 2. Wrapper de proveedores (`LLMWrapper`)

`llm_service.py` mezclaba dos cosas: el dominio (el prompt de estimación) y el proveedor (los SDKs). Ahora están separados:

```python
wrapper.complete(system, messages)  # -> dict
wrapper.stream(system, messages)    # -> LLMStream: async for trozo in stream; stream.result
```

- **Mismo dict con los dos proveedores:** `estimation`, `model`, `provider`, `usage`, `finish_reason`, `latency_ms`, `estimated_cost_usd`, `cached`. Al router no le importa quién respondió.
- **`system` y `messages` por separado.** Es lo que pide la parte 3 de la sesión 4. Allí solo cambiará de dónde salen esos strings (`render_estimation_prompt()` con Jinja2); el wrapper no se toca.
- **Se inyecta con `Depends(get_llm_wrapper)`.** En los tests se sustituye por un `FakeWrapper` con `dependency_overrides`, sin parchear funciones internas.
- **Adaptadores por proveedor:**
  - OpenAI usa la Responses API con `stream=True` (eventos `response.output_text.delta` y `response.completed`);
  - Anthropic usa `messages.stream()` (`text_stream` y `get_final_message()`).

---

## 3. Caché exact-match

- **Clave:** `sha256` del JSON canónico (`sort_keys`) de proveedor, modelo, `max_tokens`, system y messages. Cualquier cambio en el prompt da otra clave. Por eso, en la sesión 4, cambiar de versión de prompt (v1 → v2) invalida la caché sin hacer nada.
- **Almacenamiento:** en memoria, con `OrderedDict`:
  - **LRU**: al superar `CACHE_MAX_ENTRIES` sale la entrada menos usada;
  - **TTL**: `CACHE_TTL_SECONDS`, con reloj inyectable en los tests.
- **Qué se guarda:** solo respuestas completas (`finish_reason == "stop"`). Una respuesta cortada no merece repetirse.
- **Un acierto** devuelve `cached: true`, coste 0 y los tokens de la llamada original. En streaming, el texto guardado sale en un solo evento `token`.
- **Streaming y llamada normal comparten caché:** lo que guarda uno lo aprovecha el otro.
- **Límites conocidos:** se pierde al reiniciar y no se comparte entre réplicas. Para eso haría falta Redis. El cacheo semántico queda para el directo de la sesión 4.

---

## 4. Observabilidad con structlog

- **Eventos con campos, no frases:**
  - `llm_call_completed` (provider, model, tokens, latency_ms, cost_usd, cached, finish_reason);
  - `llm_call_failed`;
  - `cache_hit` / `cache_miss`;
  - `estimation_stream_started` / `estimation_stream_finished`.
- **Formato:** `ConsoleRenderer` en `development`, `JSONRenderer` en `staging` / `production`. Los logs de uvicorn pasan por el mismo formato (`ProcessorFormatter`).
- **`request_id`:** un middleware lo asigna (o reutiliza el de la cabecera `X-Request-ID`), lo enlaza con `structlog.contextvars` y lo devuelve en la respuesta. Todos los logs de una petición, incluidos los del stream, llevan el mismo id.
- **Menos ruido:** los loggers de los SDKs y de httpx/httpcore se suben a `WARNING`, porque en `DEBUG` generan decenas de líneas por llamada y `llm_call_completed` ya resume lo importante.

---

## 5. Tests (74 pasan, sin red)

| Archivo | Qué cubre |
|---|---|
| `test_llm_wrapper.py` | Parseo de los dos SDKs, normal y en streaming; coste; truncado; errores → `LLMError`; un acierto de caché no llama al proveedor; el stream usa y llena la caché |
| `test_cache.py` | Clave canónica; cualquier cambio cambia la clave; hits y misses; copia defensiva; TTL; LRU |
| `test_estimate.py` | `/estimate` y `/estimate/stream`: tokens → `done`, evento `error`, 422; cabecera `X-Request-ID` |
| `test_streamlit_app.py` | UI de punta a punta con `AppTest`: `httpx.stream` se redirige al `TestClient` de la API real, con `FakeWrapper` detrás. API caída, 422, error del proveedor, nueva conversación, y que la UI no importa `app/` ni usa keys |

---

## 6. Verificación con llamadas reales (OpenAI `gpt-4o-mini`)

| Prueba | Resultado |
|---|---|
| `curl -N` a `/estimate/stream` | Eventos `token` uno a uno y `done` con 1.248 tokens de entrada, 347 de salida, 6,5 s y $0,0004 |
| Misma petición dos veces a `/estimate` | 1.ª: `cached=false`, 6,1 s; 2.ª: `cached=true`, 0 ms, coste 0 |
| `/estimate/stream` con el mismo texto | Acierto de caché: un solo evento `token` + `done` con `cached=true` |
| UI en Chrome, transcripción nueva | La estimación se escribe en streaming; el panel muestra modelo, tokens, tiempo y coste |
| UI, la misma transcripción otra vez | Instantánea; el panel muestra "Respuesta servida desde la caché" |
| UI con la API parada | "No se puede conectar con la API en http://localhost:8000. ¿Está arrancada?" |
| Logs de uvicorn | `cache_miss` → `llm_call_completed`, y luego `cache_hit`, todos con su `request_id` |

---

## 7. Qué aporta a la sesión 4

| Sesión 4 pide… | Ya está… |
|---|---|
| Formulario que hace `POST /estimate` con JSON | Cliente HTTP con `httpx`, URL configurable, errores 422 / API caída ya tratados. Solo hay que cambiar el chat por `st.form` |
| "Mantén el wrapper de la sesión 03", con system y user separados | `LLMWrapper.complete(system, messages)` |
| `render_estimation_prompt(request) -> (system, user)` | Solo hay que sustituir `build_system_prompt()` en `llm_service.py` |
| Caché exact-match "sigue funcionando" | La clave incluye el prompt renderizado, así que cada versión de prompt tiene su propia caché |
| Bonus: logging del prompt renderizado con structlog | structlog ya está configurado; basta con un evento en el loader |
