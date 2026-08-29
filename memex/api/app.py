"""FastAPI app factory: routers, error shape, static SPA serving."""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from memex.api import chat, internal, routes
from memex.api.common import ApiError, error_body

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SPAStaticFiles(StaticFiles):
    """Static files with SPA fallback: unknown paths serve index.html."""

    async def get_response(self, path: str, scope):
        # API paths must 404 as JSON, never fall back to the SPA shell.
        if path.startswith(("api/", "internal/")):
            raise ApiError(404, "not_found", f"unknown route: /{path}")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


def _configure_logging() -> None:
    """Give memex's own loggers a handler.

    uvicorn configures only its own loggers, so without this the root logger
    has no handler and everything the app logs below ERROR is dropped —
    including "started research operation …", which is exactly the line you
    want when a background run seems not to have happened. ERROR only ever
    surfaced at all via logging's last-resort fallback.
    """
    level = os.environ.get("MEMEX_LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger("memex")
    if logger.handlers:  # configured already (reload, repeated create_app)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)


async def _inline_research_poller() -> None:
    """Drive research polls locally, where no Cloud Tasks queue exists.

    In production every poll re-enqueues the next one; with
    MEMEX_INLINE_POLL=1 nothing would ever poll a running operation, so this
    lifespan task sweeps them instead — otherwise a local research run sits
    at "running" forever (found the hard way, 2026-08-28).
    """
    from memex.agent import research  # heavy deps stay off the import path

    logger = logging.getLogger("memex.api.app")
    logger.info(
        "inline research poller running (every %ss)", research.POLL_DELAY_SECONDS
    )
    while True:
        await asyncio.sleep(research.POLL_DELAY_SECONDS)
        try:
            await asyncio.to_thread(research.poll_running_operations)
        except Exception:
            logger.exception("inline research poll sweep failed")


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    task: asyncio.Task | None = None
    if os.environ.get("MEMEX_INLINE_POLL", "") == "1":
        task = asyncio.create_task(_inline_research_poller())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    _configure_logging()
    app = FastAPI(title="memex", docs_url=None, redoc_url=None, lifespan=_lifespan)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=error_body(exc.code, exc.message)
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422, content=error_body("validation_error", str(exc.errors()))
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code, content=error_body(code, str(exc.detail))
        )

    # GFE intercepts /healthz on run.app domains (Google-404s it),
    # so /health is the deployed path; /healthz kept for local habit.
    @app.get("/health")
    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    app.include_router(routes.router)
    app.include_router(chat.router)
    app.include_router(internal.router)

    if STATIC_DIR.is_dir():
        app.mount("/", SPAStaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
