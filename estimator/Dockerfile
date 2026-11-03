# =============================================================================
# Etapa 1 — builder: instala las dependencias con uv
# =============================================================================
# Build multi-stage: uv y la caché de instalación se quedan en esta etapa, así la
# imagen final es más pequeña y tiene menos superficie de ataque.
FROM python:3.11-slim AS builder

# Binario estático de uv desde la imagen oficial (versión fijada, no "latest",
# para que el build sea reproducible)
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Primero solo los manifiestos: esta capa se cachea y las dependencias solo se
# reinstalan cuando cambian pyproject.toml o uv.lock
COPY pyproject.toml uv.lock ./

# Solo dependencias de producción (sin pytest/ruff), exactamente las del lockfile
RUN uv sync --locked --no-dev --no-install-project


# =============================================================================
# Etapa 2 — runtime: imagen limpia con el .venv y el código
# =============================================================================
FROM python:3.11-slim AS runtime

# Nunca ejecutar como root dentro del contenedor
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app app/ /app/app/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

# La imagen slim no trae curl: comprobamos /health con la stdlib de Python
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
