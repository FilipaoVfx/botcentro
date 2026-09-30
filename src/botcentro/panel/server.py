"""Servidor del panel: API /v1/admin y la SPA compilada (panel/dist).

    BOTCENTRO_INSFORGE_URL=https://<app>.insforge.app uvicorn botcentro.panel.server:app

Se despliega aparte del bot (srs-panel-web.md §3.2): no necesita credenciales de servicio,
cada operador lee con su propia sesión.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from botcentro.api.app import create_app
from botcentro.errors import BotcentroError
from botcentro.panel.routes import build_router
from botcentro.panel.sessions import InsForgeAuth, SessionExpired, SessionStore

DIST = Path(os.environ.get("BOTCENTRO_PANEL_DIST", Path(__file__).resolve().parents[3] / "panel" / "dist"))

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def create_panel_app(store: SessionStore, *, dist: Path = DIST, secure_cookie: bool = True,
                     enrich=None) -> FastAPI:  # noqa: ANN001
    app = create_app(rpc=None, telegram=None)

    @app.exception_handler(SessionExpired)
    async def expired(request: Request, exc: SessionExpired) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message, "request_id": request.state.request_id,
                             "retryable": False, "details": {}}, status_code=401)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/v1/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(build_router(store, secure_cookie=secure_cookie, enrich=enrich))

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith(("v1/", "health/")):
            raise BotcentroError("NOT_FOUND", "ruta inexistente", status=404)
        candidate = (dist / path).resolve()
        if path and dist.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse({"message": "panel sin compilar: ejecuta npm run build en panel/"}, status_code=503)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _from_env() -> FastAPI:
    secure = os.environ.get("BOTCENTRO_PANEL_INSECURE_COOKIE") != "1"
    from botcentro.panel.live_stats import LiveStats

    if os.environ.get("BOTCENTRO_DATABASE_URL"):  # PostgreSQL autoalojado (DEC-21): código por Telegram
        from botcentro.db.pgclient import conninfo_from_env, shared_pool
        from botcentro.panel.local_auth import LocalAuth, parse_operators, telegram_sender

        auth = LocalAuth(shared_pool(conninfo_from_env()), parse_operators(os.environ.get("BOTCENTRO_PANEL_OPERATORS", "")),
                         telegram_sender(os.environ["BOTCENTRO_TELEGRAM_BOT_TOKEN"]))
    else:
        base = os.environ.get("BOTCENTRO_INSFORGE_URL")
        if not base:
            raise RuntimeError("falta BOTCENTRO_DATABASE_URL o BOTCENTRO_INSFORGE_URL")
        auth = InsForgeAuth(base)
    return create_panel_app(SessionStore(auth), secure_cookie=secure, enrich=LiveStats.from_env().enrich_view)  # type: ignore[arg-type]


app = _from_env() if os.environ.get("BOTCENTRO_DATABASE_URL") or os.environ.get("BOTCENTRO_INSFORGE_URL") else None
