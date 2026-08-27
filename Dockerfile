# memex Cloud Run image: FastAPI + ADK runner + static frontend.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code edits don't bust the dep layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code, including memex/static (the built SPA) when present.
COPY memex/ memex/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080

CMD exec uvicorn memex.api.app:app --host 0.0.0.0 --port ${PORT}
