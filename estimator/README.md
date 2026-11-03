# Estimador CAG

Servicio FastAPI que recibe la transcripción de una reunión con un cliente y devuelve una **estimación de proyecto de software** generada por un LLM, que puede ser OpenAI o Anthropic.

Proyecto 1 del curso AI Engineering de LIDR. Se empezó en la sesión 2 y se irá mejorando en las siguientes.

## Arquitectura: CAG (Cache Augmented Generation)

Todo el contexto que necesita el modelo viaja en cada llamada: no hay base de datos, ni retrieval, ni persistencia. Las estimaciones de referencia son unos pocos ejemplos que caben de sobra en la ventana de contexto, y se inyectan en el *system prompt*:

```
[system]    → instrucciones + estimaciones previas (app/context/examples.py)
[user]      → transcripción de la reunión
[assistant] → estimación generada
```

| Carpeta / archivo | Responsabilidad |
|---|---|
| `app/main.py` | Aplicación FastAPI, `GET /health`, router en `/api/v1` |
| `app/config.py` | Configuración con Pydantic Settings, leída de `.env` |
| `app/schemas.py` | Contratos de entrada y salida (Pydantic) |
| `app/routers/estimations.py` | `POST /api/v1/estimate` |
| `app/services/llm_service.py` | System prompt CAG y llamadas a OpenAI o Anthropic, con clientes async |
| `app/context/examples.py` | Estimaciones de referencia: el contexto estático |
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
  "created_at": "2026-11-03T13:00:00Z"
}
```

`finish_reason: "length"` indica que la respuesta se cortó por el límite de tokens. Si el proveedor falla (key inválida, sin crédito…), el endpoint responde **502**.

## Validación automática

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
```

Los tests no llaman al LLM: lo simulan, así que no cuestan dinero. Comprueban:
- la estructura de carpetas pedida;
- la configuración;
- que los ejemplos son coherentes y están inyectados en el prompt;
- `/health`, `/docs` y `/estimate` (200, 422 y 502);
- el parseo de las respuestas de los dos SDKs.

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
