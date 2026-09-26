"""API del panel: autenticación por código y lectura operativa (srs-panel-web.md §10, §13).

Rutas bajo /v1/admin. Toda lectura se ejecuta con el JWT del operador, así que RLS decide qué
ve. Las mutaciones (POST) exigen la cabecera X-Botcentro-Panel además de la cookie
SameSite=Strict, como defensa CSRF.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from botcentro.errors import BotcentroError
from botcentro.panel.sessions import PanelSession, SessionExpired, SessionStore

log = logging.getLogger(__name__)

COOKIE = "bc_panel"
CSRF_HEADER = "x-botcentro-panel"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,}$", re.IGNORECASE)
CODE_RE = re.compile(r"^\d{6}$")

# Vista del panel → RPC de lectura y parámetros admitidos.
VIEWS: dict[str, tuple[str, set[str]]] = {
    "overview": ("ops_overview", set()),
    "sources": ("ops_sources", set()),
    "runs": ("ops_runs", {"limit"}),
    "jobs": ("ops_jobs", {"state", "limit"}),
    "documents": ("ops_documents", {"limit"}),
    "queries": ("ops_queries", {"limit"}),
    "review-cases": ("ops_review_cases", {"state", "limit"}),
    "costs": ("ops_costs", set()),
    "audit": ("ops_audit", {"limit"}),
}
JOB_STATES = {"pending", "leased", "retry_wait", "succeeded", "dead_letter", "cancelled"}
CASE_STATES = {"open", "in_review", "resolved", "dismissed"}

STREAM_POLL_SECONDS = 5.0
STREAM_HEARTBEAT_SECONDS = 15.0


class CodeRequest(BaseModel):
    email: str


class SessionRequest(BaseModel):
    email: str
    code: str


class RateLimiter:
    """Límite simple por clave en ventana deslizante (envío de códigos)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


def _error(code: str, message: str, status: int, request: Request) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, "request_id": request.state.request_id,
                         "retryable": False, "details": {}}, status_code=status)


def build_router(store: SessionStore, *, secure_cookie: bool = True) -> APIRouter:
    router = APIRouter(prefix="/v1/admin")
    code_limiter = RateLimiter(limit=5, window=600)

    def require_csrf(request: Request) -> None:
        if request.headers.get(CSRF_HEADER) != "1":
            raise BotcentroError("CSRF", "solicitud sin cabecera del panel", status=403)

    def current(request: Request) -> PanelSession:
        return store.get(request.cookies.get(COOKIE))

    @router.post("/auth/code", dependencies=[Depends(require_csrf)])
    async def send_code(body: CodeRequest, request: Request) -> JSONResponse:
        email = body.email.strip().lower()
        if not EMAIL_RE.match(email):
            return _error("INVALID_EMAIL", "Escribe un correo válido.", 422, request)
        client = request.client.host if request.client else "?"
        if not code_limiter.allow(f"{client}:{email}"):
            return _error("RATE_LIMITED", "Demasiados códigos solicitados. Espera unos minutos.", 429, request)
        await run_in_threadpool(store.auth.send_code, email)
        # Respuesta genérica: no revela si el correo existe (igual que InsForge).
        return JSONResponse({"status": "sent"}, status_code=202)

    @router.post("/auth/session", dependencies=[Depends(require_csrf)])
    async def create_session(body: SessionRequest, request: Request, response: Response) -> JSONResponse:
        email = body.email.strip().lower()
        if not EMAIL_RE.match(email) or not CODE_RE.match(body.code.strip()):
            return _error("INVALID_CODE", "El código tiene 6 dígitos.", 422, request)
        try:
            payload = await run_in_threadpool(store.auth.verify_code, email, body.code.strip())
        except BotcentroError:
            return _error("CODE_REJECTED", "Código incorrecto o vencido. Solicita uno nuevo.", 401, request)
        session = store.create(payload)
        claimed = await run_in_threadpool(store.rpc, session, "claim_pending_roles", {})
        result = JSONResponse({"status": "ok", "claimed_roles": claimed or []})
        result.set_cookie(COOKIE, session.id, httponly=True, secure=secure_cookie, samesite="strict",
                          max_age=12 * 3600, path="/")
        return result

    @router.post("/auth/logout", dependencies=[Depends(require_csrf)])
    async def logout(request: Request) -> JSONResponse:
        store.drop(request.cookies.get(COOKIE))
        result = JSONResponse({"status": "ok"})
        result.delete_cookie(COOKIE, path="/")
        return result

    @router.get("/ops/capabilities")
    async def capabilities(request: Request) -> JSONResponse:
        session = current(request)
        caps = await run_in_threadpool(store.rpc, session, "ops_capabilities", {})
        return JSONResponse({**caps, "email": session.email, "request_id": request.state.request_id})

    @router.get("/ops/sources/{source_id}")
    async def source_detail(source_id: UUID, request: Request) -> JSONResponse:
        session = current(request)
        data = await run_in_threadpool(store.rpc, session, "ops_source", {"p_id": source_id})
        if data is None:
            return _error("NOT_FOUND", "La fuente no existe o no es visible.", 404, request)
        return JSONResponse({**data, "request_id": request.state.request_id})

    @router.get("/ops/stream")
    async def stream(request: Request) -> StreamingResponse:
        session = current(request)

        async def events():
            last_hash = None
            last_beat = time.monotonic()
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    data = await run_in_threadpool(store.rpc, session, "ops_overview", {})
                except SessionExpired:
                    yield "event: session_expired\ndata: {}\n\n"
                    return
                except BotcentroError as exc:
                    yield f"event: degraded\ndata: {json.dumps({'code': exc.code})}\n\n"
                    data = None
                if data is not None:
                    comparable = {k: v for k, v in data.items() if k != "as_of"}
                    digest = hashlib.sha256(json.dumps(comparable, sort_keys=True).encode()).hexdigest()
                    if digest != last_hash:
                        last_hash = digest
                        yield f"event: overview\ndata: {json.dumps(data)}\n\n"
                        last_beat = time.monotonic()
                    elif time.monotonic() - last_beat >= STREAM_HEARTBEAT_SECONDS:
                        yield f"event: heartbeat\ndata: {json.dumps({'as_of': data.get('as_of')})}\n\n"
                        last_beat = time.monotonic()
                await asyncio.sleep(STREAM_POLL_SECONDS)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.get("/ops/{view}")
    async def read_view(view: str, request: Request) -> JSONResponse:
        if view not in VIEWS:
            return _error("NOT_FOUND", "Vista inexistente.", 404, request)
        session = current(request)
        fn, allowed = VIEWS[view]
        params: dict[str, Any] = {}
        for name, value in request.query_params.items():
            if name not in allowed:
                continue
            if name == "limit":
                if not value.isdigit():
                    return _error("INVALID_FILTER", "limit debe ser numérico.", 422, request)
                params["p_limit"] = min(int(value), 100)
            elif name == "state":
                if value not in (JOB_STATES if view == "jobs" else CASE_STATES):
                    return _error("INVALID_FILTER", "Estado no reconocido.", 422, request)
                params["p_state"] = value
        data = await run_in_threadpool(store.rpc, session, fn, params)
        return JSONResponse({**data, "request_id": request.state.request_id})

    return router
