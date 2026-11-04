# Estimador CAG

Servicio FastAPI que recibe la transcripción de una reunión con un cliente y devuelve una **estimación de proyecto de software** generada por un LLM, que puede ser OpenAI o Anthropic. Incluye un cliente web de chat hecho con Streamlit.

Proyecto 1 del curso AI Engineering de LIDR. Se empezó en la sesión 2 y se irá mejorando en las siguientes.

```
Streamlit (UI) ──HTTP/SSE──▶ FastAPI ──▶ llm_service (prompt CAG) ──▶ LLMWrapper ──▶ OpenAI / Anthropic
 sin API keys                 /api/v1                                  caché exact-match
                                                                       logs (structlog)
```

## Arquitectura: CAG (Cache Augmented Generation)

Todo el contexto que necesita el modelo viaja en cada llamada: no hay base de datos, ni retrieval, ni persistencia. Las estimaciones de referencia son unos pocos ejemplos que caben de sobra en la ventana de contexto, y se inyectan en el *system prompt*:

```
[system]    → instrucciones + estimaciones previas (app/context/examples.py)
[user]      → transcripción de la reunión
[assistant] → estimación generada
```

| Carpeta / archivo | Responsabilidad |
|---|---|
| `app/main.py` | Aplicación FastAPI, `GET /health`, router en `/api/v1`, middleware de `request_id` |
| `app/config.py` | Configuración con Pydantic Settings, leída de `.env` |
| `app/logging_config.py` | Logging estructurado con structlog: consola en desarrollo, JSON en producción |
| `app/schemas.py` | Contratos de entrada y salida (Pydantic) |
| `app/routers/estimations.py` | `POST /api/v1/estimate` y `POST /api/v1/estimate/stream` (SSE) |
| `app/services/llm_service.py` | Dominio: el system prompt CAG y qué se le pide al LLM |
| `app/services/llm_wrapper.py` | Wrapper de proveedores: OpenAI y Anthropic tras una sola interfaz, normal o en streaming; caché, coste y logs de cada llamada |
| `app/services/cache.py` | Caché exact-match en memoria (LRU + TTL) |
| `app/context/examples.py` | Estimaciones de referencia: el contexto estático |
| `streamlit_app.py` | Cliente web de chat: habla con la API por HTTP/SSE |
| `data/transcripcion_ejemplo.txt` | Transcripción de prueba del enunciado |

## Puesta en marcha

```bash
cd estimator
cp .env.example .env     # y pon tu API key
uv sync
uv run uvicorn app.main:app --reload
```

- Swagger: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

### Interfaz web (Streamlit)

Con la API arrancada, en otra terminal:

```bash
cd estimator
uv run streamlit run streamlit_app.py     # http://localhost:8501
```

La UI es un **cliente HTTP puro**: no importa código de `app/` ni conoce ninguna API key. Solo necesita la URL de la API, que lee de `ESTIMATOR_API_URL` (por defecto `http://localhost:8000`).

- **Chat:** pega una transcripción y la estimación aparece en streaming.
- **Cada mensaje es una estimación nueva**: el historial solo se ve en pantalla, no se envía al LLM.
- **Panel lateral:** el modelo, los tokens, el tiempo y el coste de la última llamada, y si la respuesta vino de la caché.
- **Errores:** se muestran en el chat, por ejemplo si la API está parada, si la transcripción es demasiado corta o si falla el proveedor.
- **Fuera de la imagen Docker:** Streamlit y httpx están en el grupo de dependencias `ui`. `uv sync` los instala, pero la imagen de la API no.

> La configuración se cachea al arrancar. Si cambias `.env` (por ejemplo, el `LLM_PROVIDER`), **reinicia uvicorn**: `--reload` solo detecta cambios en el código.

### Con Docker

```bash
cd estimator
cp .env.example .env     # y pon tu API key
docker compose up --build
```

- La imagen es multi-stage, con las dependencias instaladas por uv desde `uv.lock`. Corre con un usuario **no root** y trae un `HEALTHCHECK` sobre `/health`.
- `docker compose` lee las keys de `.env` al arrancar. El `.env` **nunca entra en la imagen**: lo excluye `.dockerignore`.
- En desarrollo, compose monta `./app` dentro del contenedor y arranca con `--reload`, así que los cambios en el código se aplican sin reconstruir la imagen. Para producción hay que quitar el volumen y el `--reload`.
- Si cambias `.env`, ejecuta `docker compose up -d` para recrear el contenedor; un simple restart no recarga las variables.
- Para pararlo: `docker compose down`.

### Variables de entorno

| Variable | Descripción | Por defecto |
|---|---|---|
| `OPENAI_API_KEY` | API key de OpenAI | Obligatoria si `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | API key de Anthropic | Obligatoria si `LLM_PROVIDER=anthropic` |
| `LLM_PROVIDER` | `openai` o `anthropic` | `openai` |
| `LLM_MODEL` | Modelo que se usa | `gpt-4o-mini`, o `claude-haiku-4-5` si el proveedor es Anthropic |
| `APP_ENV` | `development`, `staging` o `production` | `development` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` o `ERROR` | `DEBUG` |
| `CACHE_ENABLED` | Activa la caché exact-match de respuestas | `true` |
| `CACHE_TTL_SECONDS` | Caducidad de cada entrada de la caché | `86400` (24 h) |
| `CACHE_MAX_ENTRIES` | Máximo de entradas; al llenarse sale la menos usada (LRU) | `256` |
| `ESTIMATOR_API_URL` | Solo para la UI: URL de la API | `http://localhost:8000` |

## Probar el endpoint

```bash
curl -s -X POST http://localhost:8000/api/v1/estimate \
  -H "Content-Type: application/json" \
  -d "{\"transcription\": $(jq -Rs . < data/transcripcion_ejemplo.txt)}" | jq
```

Ejemplo de respuesta (resumida):

```json
{
  "estimation": "## Estimación: Landing page con CRM y blog\n\n| Tarea | Perfil | Horas | Coste |...",
  "model": "gpt-4o-mini-2024-07-18",
  "provider": "openai",
  "usage": {"input_tokens": 1650, "output_tokens": 620, "total_tokens": 2270},
  "finish_reason": "stop",
  "latency_ms": 8400,
  "estimated_cost_usd": 0.000620,
  "cached": false,
  "created_at": "2026-11-03T13:00:00Z"
}
```

- `finish_reason: "length"` indica que la respuesta se cortó por el límite de tokens.
- Si el proveedor falla (key inválida, sin crédito…), el endpoint responde **502**.
- Si repites exactamente la misma petición, la respuesta sale de la caché: `cached: true`, latencia de pocos milisegundos y coste 0. Los tokens que se muestran son los de la llamada original.

### En streaming (Server-Sent Events)

`POST /api/v1/estimate/stream` recibe el mismo body y devuelve la estimación token a token. `-N` desactiva el buffer de curl para ver los eventos según llegan:

```bash
curl -N -X POST http://localhost:8000/api/v1/estimate/stream \
  -H "Content-Type: application/json" \
  -d "{\"transcription\": $(jq -Rs . < data/transcripcion_ejemplo.txt)}"
```

```
event: token
data: "## Est"

event: token
data: "imación"

...

event: done
data: {"model":"gpt-4o-mini-2024-07-18","provider":"openai","usage":{...},"latency_ms":6490,"cached":false,...}
```

| Evento | `data` (siempre JSON) |
|---|---|
| `token` | Trozo de texto de la estimación |
| `done` | Metadatos de la llamada: los mismos campos que `/estimate`, sin `estimation` |
| `error` | `{"detail": "..."}` si el proveedor falla a mitad del stream. La respuesta ya empezó con 200, así que el error no puede ser un código HTTP |

### Logs

Cada petición lleva un `request_id`, que se devuelve en la cabecera `X-Request-ID` (o se reutiliza si el cliente la envía), y todos sus logs lo incluyen. Eventos principales:

- `llm_call_completed`: proveedor, modelo, tokens, latencia, coste, `cached`;
- `llm_call_failed`;
- `cache_hit` / `cache_miss` (en `DEBUG`);
- `estimation_stream_started` / `estimation_stream_finished`.

En `APP_ENV=development` se ven legibles en consola; en `staging` y `production` salen en JSON, una línea por evento.

## Validación automática

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Los tests no llaman al LLM: lo simulan, así que no cuestan dinero. Comprueban:
- la estructura de carpetas pedida;
- la configuración;
- que los ejemplos son coherentes y están inyectados en el prompt;
- `/health`, `/docs`, `/estimate` (200, 422 y 502) y `/estimate/stream` (eventos `token`, `done` y `error`, y 422), con un wrapper falso;
- el wrapper: el parseo de los dos SDKs, normal y en streaming, el coste, el truncado y los errores;
- la caché: clave, aciertos, caducidad y expulsión LRU, y que un acierto no llama al proveedor;
- la UI de punta a punta con `AppTest`: la UI llama a la API real por SSE, con el wrapper falso detrás; también la API caída, los 422 y los errores del proveedor;
- que la UI no importa `app/` ni usa API keys;
- la cabecera `X-Request-ID`.

El workflow [`.github/workflows/estimator.yml`](../.github/workflows/estimator.yml) ejecuta todo esto en cada push que toque `estimator/`. Después arranca el servicio y comprueba que `/health` y `/docs` responden. Por último, construye la imagen Docker y verifica que el contenedor queda *healthy*, que corre sin root y que no incluye el `.env`.

## Checklist del ejercicio (sesión 2)

- [x] Arranca con `uv run uvicorn app.main:app --reload`
- [x] Las API keys se cargan desde `.env` y nunca aparecen en el código
- [x] `GET /health` responde 200
- [x] `POST /api/v1/estimate` recibe una transcripción y devuelve una estimación
- [x] La estimación se inspira en los ejemplos de contexto inyectados
- [x] Swagger accesible en `/docs`
- [x] `.env` está en `.gitignore`
- [x] Arranca con Docker (`docker compose up --build`)

## Checklist del ejercicio (sesión 3)

Versión UI → HTTP/SSE → API. Solo incluye lo que sirve como punto de partida para la sesión 4, más lo que se construyó en el directo:

- [x] `streamlit run streamlit_app.py` abre una interfaz de chat en el navegador
- [x] Se pega una transcripción y se recibe una estimación de software
- [x] La conversación persiste en pantalla (`st.session_state`)
- [x] La respuesta se muestra en streaming (SSE + `st.write_stream`)
- [x] La API key solo la tiene la API (`.env`); la UI no la conoce
- [x] Wrapper de proveedores (`LLMWrapper`): `system` y `messages` por separado
- [x] Caché exact-match de respuestas
- [x] Observabilidad con structlog y `request_id`
