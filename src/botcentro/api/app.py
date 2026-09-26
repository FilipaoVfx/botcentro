"""API HTTP: salud y recepción de Telegram (SRS §10). Errores con formato uniforme (§10.2):
code, message, request_id, retryable y details sanitizado; nunca secretos, SQL ni prompts.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from botcentro import __version__
from botcentro.errors import BotcentroError
from botcentro.insforge.client import RpcClient
from botcentro.telegram.security import SECRET_HEADER
from botcentro.telegram.webhook import TelegramWebhook, WebhookUnauthorized

log = logging.getLogger(__name__)

_STATUS_BY_KIND = {
    "permission": 403,
    "not_found": 404,
    "invalid_content": 422,
    "policy": 409,
    "rate_limited": 429,
    "transient": 503,
}


def error_body(code: str, message: str, request_id: str, *, retryable: bool = False,
               details: dict[str, object] | None = None) -> dict[str, object]:
    return {"code": code, "message": message, "request_id": request_id, "retryable": retryable,
            "details": details or {}}


def create_app(*, rpc: RpcClient | None, telegram: TelegramWebhook | None) -> FastAPI:
    app = FastAPI(title="botcentro", version=__version__, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(BotcentroError)
    async def handle_known(request: Request, exc: BotcentroError) -> JSONResponse:
        status = exc.status if exc.status and exc.status < 500 else _STATUS_BY_KIND.get(exc.kind.value, 500)
        return JSONResponse(error_body(exc.code, exc.message, request.state.request_id, retryable=exc.retryable),
                            status_code=status)

    @app.exception_handler(Exception)
    async def handle_unknown(request: Request, exc: Exception) -> JSONResponse:
        log.exception("error no controlado en %s", request.url.path)
        return JSONResponse(error_body("INTERNAL", "error interno", request.state.request_id, retryable=True),
                            status_code=500)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(request: Request) -> JSONResponse:
        if rpc is None:
            return JSONResponse(error_body("NOT_CONFIGURED", "sin conexión a InsForge configurada",
                                           request.state.request_id), status_code=503)
        try:
            authorized = bool(rpc.call("is_staff_or_service", {}))
        except BotcentroError as exc:
            return JSONResponse(error_body("DEPENDENCY_UNAVAILABLE", exc.message, request.state.request_id,
                                           retryable=True), status_code=503)
        if not authorized:
            return JSONResponse(error_body("SERVICE_ROLE_MISSING", "la cuenta de servicio no tiene rol asignado",
                                           request.state.request_id), status_code=503)
        return JSONResponse({"status": "ready"})

    @app.post("/v1/integrations/telegram/webhook")
    async def telegram_webhook(request: Request) -> JSONResponse:
        if telegram is None:
            return JSONResponse(error_body("NOT_CONFIGURED", "Telegram no está habilitado",
                                           request.state.request_id), status_code=404)
        body = await request.body()
        try:
            result = await run_in_threadpool(telegram.handle, request.headers.get(SECRET_HEADER), body)
        except WebhookUnauthorized:
            return JSONResponse(error_body("UNAUTHENTICATED", "entrega no autenticada", request.state.request_id),
                                status_code=401)
        if result.reply is not None:
            return JSONResponse(result.reply)
        return JSONResponse({"ok": True})

    return app
