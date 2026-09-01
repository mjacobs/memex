# memex Cloud Run image: FastAPI + ADK runner + static frontend.
# Pinned by digest so a base-image rebuild cannot change the deployed image
# unannounced. Bump deliberately: `skopeo inspect --no-tags docker://python:3.13-slim --format "{{.Digest}}"`.
FROM python:3.13-slim@sha256:881d80734ee05dca6f7f42dcb080975652a53c7eda9ba1f03bb8da31aa6a6ec2

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

RUN useradd --create-home --uid 1000 memex
USER memex

CMD exec uvicorn memex.api.app:app --host 0.0.0.0 --port ${PORT}
