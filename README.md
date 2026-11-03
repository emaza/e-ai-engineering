# e-ai-engineering

Ejercicios del curso **AI Engineering** de [LIDR](https://www.lidr.co/ai-engineering/) (edición octubre 2026).

## Organización

Cada sesión se desarrolla en su propia rama (`session_1`, `session_2`, …). El material de la sesión en vivo va en `session_N_live`.

Desde la sesión 2, el **Proyecto 1** (estimador de software con IA) vive en [`estimator/`](estimator/README.md) y evoluciona de sesión en sesión.

| Sesión | Rama | Ejercicio |
|---|---|---|
| 1 | `session_1` | Tu primera llamada a un LLM vía API (`session_1/`) |
| 2 | `session_2` | [Estimador CAG con FastAPI](estimator/README.md) |

## Documentación

Notas y análisis de las sesiones en [`docs/`](docs/README.md).

## Stack

Python 3.11+ · [uv](https://docs.astral.sh/uv/) · FastAPI · Docker Compose · Streamlit

Las API keys van en un `.env` local (nunca se commitea); cada sesión incluye su `.env.example`.
