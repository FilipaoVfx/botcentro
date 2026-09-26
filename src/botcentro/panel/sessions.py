"""Sesiones del panel (srs-panel-web.md §15.1).

Los tokens de InsForge del operador viven solo en el servidor; el navegador recibe un
identificador opaco en una cookie HttpOnly. Las sesiones expiran por inactividad y por edad
máxima, y se renuevan con el refresh token de InsForge.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from botcentro.errors import FailureKind
from botcentro.insforge.client import InsForgeError, _jwt_expiry, _raise_for_response, as_rows, encode_json

IDLE_SECONDS = 2 * 3600
MAX_AGE_SECONDS = 12 * 3600
REFRESH_MARGIN_SECONDS = 60


class SessionExpired(InsForgeError):
    kind = FailureKind.AUTH

    def __init__(self, message: str = "la sesión expiró; vuelve a entrar") -> None:
        super().__init__("SESSION_EXPIRED", message, status=401)


@dataclass
class PanelSession:
    id: str
    user_id: str
    email: str
    access_token: str
    refresh_token: str | None
    access_expires_at: float
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class InsForgeAuth:
    """Operaciones de InsForge Auth en nombre del operador."""

    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 20.0) -> None:
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                 headers={"Content-Type": "application/json"})

    def send_code(self, email: str) -> None:
        _raise_for_response(self.http.post("/api/auth/email/send-otp", json={"email": email}))

    def verify_code(self, email: str, code: str) -> Mapping[str, Any]:
        response = self.http.post("/api/auth/sessions", params={"client_type": "server"},
                                  json={"method": "otp", "email": email, "otp": code})
        _raise_for_response(response)
        return response.json()

    def refresh(self, refresh_token: str) -> Mapping[str, Any]:
        response = self.http.post("/api/auth/refresh", params={"client_type": "server"},
                                  json={"refreshToken": refresh_token})
        _raise_for_response(response)
        return response.json()

    def rpc(self, token: str, fn: str, params: Mapping[str, Any] | None = None) -> Any:
        response = self.http.post(f"/api/database/rpc/{fn}", content=encode_json(dict(params or {})),
                                  headers={"Authorization": f"Bearer {token}"})
        _raise_for_response(response)
        return response.json() if response.content else None


class SessionStore:
    def __init__(self, auth: InsForgeAuth) -> None:
        self.auth = auth
        self._sessions: dict[str, PanelSession] = {}
        self._lock = threading.Lock()

    def create(self, body: Mapping[str, Any]) -> PanelSession:
        user = body.get("user") or {}
        access = body.get("accessToken")
        if not access or not user.get("id"):
            raise InsForgeError("AUTH_NO_TOKEN", "InsForge no devolvió una sesión válida", kind=FailureKind.AUTH)
        session = PanelSession(
            id=secrets.token_urlsafe(32),
            user_id=str(user["id"]),
            email=str(user.get("email") or ""),
            access_token=access,
            refresh_token=body.get("refreshToken"),
            access_expires_at=_jwt_expiry(access) or time.time() + 300,
        )
        with self._lock:
            self._purge()
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> PanelSession:
        now = time.time()
        with self._lock:
            session = self._sessions.get(session_id or "")
            if session is None or now - session.last_seen_at > IDLE_SECONDS or now - session.created_at > MAX_AGE_SECONDS:
                if session is not None:
                    self._sessions.pop(session.id, None)
                raise SessionExpired()
            session.last_seen_at = now
            return session

    def drop(self, session_id: str | None) -> None:
        with self._lock:
            self._sessions.pop(session_id or "", None)

    def token(self, session: PanelSession) -> str:
        with session.lock:
            if time.time() >= session.access_expires_at - REFRESH_MARGIN_SECONDS:
                if not session.refresh_token:
                    self.drop(session.id)
                    raise SessionExpired()
                try:
                    body = self.auth.refresh(session.refresh_token)
                except InsForgeError as exc:
                    self.drop(session.id)
                    raise SessionExpired() from exc
                session.access_token = body["accessToken"]
                session.refresh_token = body.get("refreshToken") or session.refresh_token
                session.access_expires_at = _jwt_expiry(session.access_token) or time.time() + 300
            return session.access_token

    def rpc(self, session: PanelSession, fn: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.auth.rpc(self.token(session), fn, params)

    def _purge(self) -> None:
        now = time.time()
        for sid in [s.id for s in self._sessions.values()
                    if now - s.last_seen_at > IDLE_SECONDS or now - s.created_at > MAX_AGE_SECONDS]:
            self._sessions.pop(sid, None)


__all__ = ["InsForgeAuth", "PanelSession", "SessionExpired", "SessionStore", "as_rows"]
