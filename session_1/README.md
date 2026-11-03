# Sesión 1 — Tu primera llamada a un LLM vía API

Notebook: [`primera_llamada_llm.ipynb`](primera_llamada_llm.ipynb)

Hace la primera integración real con un LLM desde Python: envía un mensaje, recibe la respuesta y muestra cómo cambia el comportamiento del modelo según las instrucciones que recibe. Funciona con **OpenAI** y con **Anthropic**: si solo hay key de uno de los dos, las celdas del otro se saltan con un aviso.

| Nivel | Contenido | |
|---|---|---|
| 1 | Llamada básica: un mensaje `user` y `print` de la respuesta | obligatorio |
| 2 | El mismo mensaje con un *system prompt* de experto en estimación, comparado lado a lado con el nivel 1 y con métricas de forma | obligatorio |
| 3 | Tokens de entrada y salida, modelo y coste estimado de las 4 llamadas | opcional |

## Ejecutar en Google Colab

1. *File → Upload notebook* y sube `primera_llamada_llm.ipynb` (o ábrelo desde GitHub).
2. En el panel 🔑 **Secrets**, crea `OPENAI_API_KEY` y/o `ANTHROPIC_API_KEY` y activa el acceso del notebook.
3. Opcional: crea también `OPENAI_MODEL` / `ANTHROPIC_MODEL` para cambiar de modelo.
4. *Runtime → Run all*. La primera celda instala `openai` y `anthropic`.

## Ejecutar en local

```bash
cd session_1
cp .env.example .env      # y rellena las keys
uv sync                   # crea .venv con openai, anthropic, python-dotenv, ipykernel
```

Abre el notebook en VS Code (o Jupyter) con el kernel de `session_1/.venv`. Para ejecutarlo entero desde la terminal:

```bash
uv run jupyter nbconvert --to notebook --execute primera_llamada_llm.ipynb --output-dir /tmp --output salida.ipynb
```

## Modelos y precios

| Proveedor | Modelo por defecto | USD / 1M tokens (entrada / salida) |
|---|---|---|
| OpenAI | `gpt-4o-mini` | 0.15 / 0.60 ⚠️ verificar en <https://platform.openai.com/pricing> |
| Anthropic | `claude-opus-5-5` | 4.00 / 20.00 |
| Anthropic (alternativa barata) | `claude-haiku-5-5` | 0.10 / 0.50 |

Los precios están en el dict `PRICES_PER_1M` del nivel 3. Revísalos en las páginas oficiales, porque cambian.

## Problemas comunes

- **`AuthenticationError` / 401:** la key está mal copiada, o no es la del proveedor correcto.
- **`RateLimitError` / 429 o error de cuota:** la cuenta no tiene crédito.
- **`NotFoundError` / 404 del modelo:** el id de modelo está mal escrito, o tu cuenta no tiene acceso a ese modelo.
- **`ModuleNotFoundError`:** en local, ejecuta `uv sync` y elige el kernel de `.venv`. En Colab, vuelve a ejecutar la primera celda.
- **"sin key (se saltará)" en Colab:** el secreto no existe o no tiene activado el acceso para este notebook.
