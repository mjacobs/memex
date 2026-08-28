"""FastAPI app factory: routers, error shape, static SPA serving."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from memex.api import internal, routes
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


def create_app() -> FastAPI:
    app = FastAPI(title="memex", docs_url=None, redoc_url=None)

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
    app.include_router(internal.router)

    if STATIC_DIR.is_dir():
        app.mount("/", SPAStaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
