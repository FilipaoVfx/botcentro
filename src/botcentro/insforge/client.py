"""Cliente HTTP de InsForge para los servicios (ingesta, consulta, bot).

Los servicios se autentican como usuarios de InsForge Auth con un rol en public.app_roles
(cuentas de servicio) y no con la API key administrativa (SRS-N01: permisos mínimos). Todas
las escrituras sensibles pasan por funciones RPC que validan el rol en la base.

Endpoints usados (docs de InsForge, REST):
  POST /api/auth/sessions?client_type=server   → accessToken + refreshToken
  POST /api/auth/refresh?client_type=server    → rota ambos tokens
  POST /api/database/rpc/{fn}                   → función SQL con parámetros nombrados
  GET/POST/PATCH /api/database/records/{table}  → CRUD estilo PostgREST sujeto a RLS
"""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import httpx

from botcentro.errors import BotcentroError, FailureKind


class RpcClient(Protocol):
    """Invoca una función SQL de public con parámetros nombrados (semántica PostgREST)."""

    def call(self, fn: str, params: Mapping[str, Any] | None = None) -> Any: ...


class InsForgeError(BotcentroError):
    """Error devuelto por InsForge/PostgREST o por las funciones SQL (códigos BCxxx)."""

    def __init__(self, code: str, message: str, *, sqlstate: str | None = None, status: int | None = None,
                 detail: str | None = None, kind: FailureKind | None = None) -> None:
        super().__init__(code, message, kind=kind, status=status)
        self.sqlstate = sqlstate
        self.detail = detail


class PermissionDenied(InsForgeError):
    kind = FailureKind.PERMISSION


class NotFound(InsForgeError):
    kind = FailureKind.NOT_FOUND


class InvalidArgument(InsForgeError):
    kind = FailureKind.INVALID_CONTENT


class InvalidState(InsForgeError):
    """BC004: la operación no aplica al estado actual (fuente no activa, caso cerrado...)."""

    kind = FailureKind.POLICY


class ActivationRejected(InsForgeError):
    """BC002: la fuente no cumple los requisitos de activación (T-01)."""

    kind = FailureKind.POLICY


class IntegrityViolation(InsForgeError):
    """BC001/BC003 y violaciones de restricciones: nunca se reintentan a ciegas."""

    kind = FailureKind.POLICY


_SQLSTATE_ERRORS: dict[str, type[InsForgeError]] = {
    "42501": PermissionDenied,
    "P0002": NotFound,
    "22023": InvalidArgument,
    "BC001": IntegrityViolation,
    "BC002": ActivationRejected,
    "BC003": IntegrityViolation,
    "BC004": InvalidState,
    "23505": IntegrityViolation,
    "23503": IntegrityViolation,
    "23514": IntegrityViolation,
}


def error_from_sqlstate(sqlstate: str | None, message: str, *, detail: str | None = None,
                        status: int | None = None) -> InsForgeError:
    cls = _SQLSTATE_ERRORS.get(sqlstate or "", InsForgeError)
    kind = None
    if cls is InsForgeError:
        kind = FailureKind.TRANSIENT if (status or 0) >= 500 or status == 429 else FailureKind.UNKNOWN
    return cls(sqlstate or f"HTTP_{status}", message, sqlstate=sqlstate, status=status, detail=detail, kind=kind)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"no serializable: {type(value).__name__}")


def encode_json(value: Any) -> bytes:
    return json.dumps(value, default=_json_default, ensure_ascii=False).encode("utf-8")


def as_rows(result: Any) -> list[dict[str, Any]]:
    """Normaliza el resultado de una RPC de tabla/conjunto a una lista de filas."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return [result]
    raise TypeError(f"se esperaban filas y llegó {type(result).__name__}")


def _jwt_expiry(token: str) -> float | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError, TypeError):
        return None


def _raise_for_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    sqlstate = body.get("code") if isinstance(body.get("code"), str) else None
    message = str(body.get("message") or body.get("error") or response.reason_phrase or "error de InsForge")
    detail = body.get("details") or body.get("detail")
    raise error_from_sqlstate(sqlstate, message, detail=str(detail) if detail else None, status=response.status_code)


class ServiceSession:
    """Sesión de una cuenta de servicio con renovación automática del access token."""

    REFRESH_MARGIN_SECONDS = 60

    def __init__(self, http: httpx.Client, email: str, password: str) -> None:
        self._http = http
        self._email = email
        self._password = password
        self._access: str | None = None
        self._refresh: str | None = None
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    def access_token(self) -> str:
        with self._lock:
            if self._access is None or time.time() >= self._expires_at - self.REFRESH_MARGIN_SECONDS:
                self._renew()
            assert self._access is not None
            return self._access

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0.0

    def _renew(self) -> None:
        if self._refresh is not None:
            response = self._http.post("/api/auth/refresh", params={"client_type": "server"},
                                       json={"refreshToken": self._refresh})
            if response.is_success:
                self._store(response.json())
                return
        response = self._http.post(
            "/api/auth/sessions",
            params={"client_type": "server"},
            json={"method": "password", "email": self._email, "password": self._password},
        )
        _raise_for_response(response)
        self._store(response.json())

    def _store(self, body: Mapping[str, Any]) -> None:
        access = body.get("accessToken")
        if not access:
            raise InsForgeError("AUTH_NO_TOKEN", "InsForge no devolvió accessToken (¿email sin verificar?)",
                                kind=FailureKind.AUTH)
        self._access = access
        self._refresh = body.get("refreshToken") or self._refresh
        self._expires_at = _jwt_expiry(access) or time.time() + 300


class InsForgeClient:
    """RPC y registros con la identidad de una cuenta de servicio."""

    def __init__(self, base_url: str, email: str, password: str, *, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                  headers={"Content-Type": "application/json"})
        self.session = ServiceSession(self._http, email, password)

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, params: Mapping[str, Any] | None = None,
                 body: Any = None, headers: Mapping[str, str] | None = None) -> httpx.Response:
        for attempt in (1, 2):
            request_headers = {"Authorization": f"Bearer {self.session.access_token()}", **(headers or {})}
            response = self._http.request(method, path, params=params, headers=request_headers,
                                          content=encode_json(body) if body is not None else None)
            if response.status_code == 401 and attempt == 1:
                self.session.invalidate()
                continue
            _raise_for_response(response)
            return response
        raise AssertionError("inalcanzable")

    def call(self, fn: str, params: Mapping[str, Any] | None = None) -> Any:
        response = self._request("POST", f"/api/database/rpc/{fn}", body=dict(params or {}))
        if not response.content:
            return None
        return response.json()

    def select(self, table: str, params: Mapping[str, str]) -> list[dict[str, Any]]:
        return as_rows(self._request("GET", f"/api/database/records/{table}", params=params).json())

    def insert(self, table: str, rows: Sequence[Mapping[str, Any]], *, returning: bool = True) -> list[dict[str, Any]]:
        headers = {"Prefer": "return=representation"} if returning else {}
        response = self._request("POST", f"/api/database/records/{table}", body=list(rows), headers=headers)
        return as_rows(response.json()) if returning and response.content else []

    def update(self, table: str, filters: Mapping[str, str], values: Mapping[str, Any]) -> list[dict[str, Any]]:
        response = self._request("PATCH", f"/api/database/records/{table}", params=filters, body=dict(values),
                                 headers={"Prefer": "return=representation"})
        return as_rows(response.json()) if response.content else []
