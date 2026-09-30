"""Acceso al panel sin InsForge Auth (DEC-21): código de un solo uso enviado por Telegram.

Cada operador se declara en `BOTCENTRO_PANEL_OPERATORS` como `correo=chat_id` (separados por
comas) y debe existir en auth.users con un rol operativo. El código (6 dígitos, 10 minutos, cinco
intentos) se envía al chat privado del operador con el bot de producción. Un correo desconocido
recibe la misma respuesta que uno válido: no se revela quién es operador.

La sesión del panel conserva la interfaz de `InsForgeAuth` (`send_code`, `verify_code`, `refresh`,
`rpc`). El token de acceso es opaco y vive solo en el servidor; las lecturas se ejecutan con
`PgClient` como ese usuario, de modo que los roles y RLS deciden igual que antes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from botcentro.db.pgclient import PgClient
from botcentro.errors import FailureKind
from botcentro.insforge.client import InsForgeError

CODE_TTL = 600
MAX_ATTEMPTS = 5
TOKEN_TTL = 12 * 3600


def parse_operators(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        email, _, chat = item.partition("=")
        if email and chat.strip().lstrip("-").isdigit():
            out[email.strip().lower()] = int(chat)
    return out


def telegram_sender(token: str, *, transport: httpx.BaseTransport | None = None) -> Callable[[int, str], None]:
    client = httpx.Client(base_url="https://api.telegram.org", timeout=15, transport=transport)

    def send(chat_id: int, text: str) -> None:
        response = client.post(f"/bot{token}/sendMessage", json={"chat_id": chat_id, "text": text})
        if response.status_code >= 400:
            raise InsForgeError("CODE_NOT_SENT", "no se pudo enviar el código", status=502, kind=FailureKind.TRANSIENT)

    return send


class LocalAuth:
    def __init__(self, pool: Any, operators: Mapping[str, int], send: Callable[[int, str], None], *,
                 clock: Callable[[], float] = time.time) -> None:
        self.pool = pool
        self.operators = {k.lower(): v for k, v in operators.items()}
        self.send = send
        self.clock = clock
        self._codes: dict[str, tuple[str, float, int]] = {}  # correo → (hash, vence, intentos)
        self._tokens: dict[str, tuple[str, float]] = {}  # token → (user_id, vence)
        self._lock = threading.Lock()
        self._salt = secrets.token_bytes(16)

    def _hash(self, email: str, code: str) -> str:
        return hmac.new(self._salt, f"{email}|{code}".encode(), hashlib.sha256).hexdigest()

    def _user(self, email: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute("select id, email from public.panel_operator(%s)", (email,)).fetchone()

    def send_code(self, email: str) -> None:
        email = email.strip().lower()
        chat = self.operators.get(email)
        if chat is None or self._user(email) is None:
            return  # misma respuesta que un correo válido
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            self._codes[email] = (self._hash(email, code), self.clock() + CODE_TTL, 0)
        self.send(chat, f"Código de acceso al panel de botcentro: {code}\nVence en 10 minutos. Si no lo pediste, ignóralo.")

    def verify_code(self, email: str, code: str) -> Mapping[str, Any]:
        email = email.strip().lower()
        with self._lock:
            entry = self._codes.get(email)
            if entry is None or self.clock() > entry[1] or entry[2] >= MAX_ATTEMPTS:
                self._codes.pop(email, None)
                raise InsForgeError("INVALID_CODE", "código inválido o vencido", status=400, kind=FailureKind.AUTH)
            if not hmac.compare_digest(entry[0], self._hash(email, code.strip())):
                self._codes[email] = (entry[0], entry[1], entry[2] + 1)
                raise InsForgeError("INVALID_CODE", "código inválido o vencido", status=400, kind=FailureKind.AUTH)
            self._codes.pop(email, None)
        user = self._user(email)
        if user is None:
            raise InsForgeError("INVALID_CODE", "código inválido o vencido", status=400, kind=FailureKind.AUTH)
        expires = self.clock() + TOKEN_TTL
        # Forma de JWT solo para que la sesión lea el vencimiento; la validez la decide `_tokens`.
        payload = base64.urlsafe_b64encode(json.dumps({"exp": expires}).encode()).rstrip(b"=").decode()
        token = f"local.{payload}.{secrets.token_urlsafe(32)}"
        with self._lock:
            self._tokens[token] = (str(user["id"]), expires)
        return {"user": {"id": str(user["id"]), "email": user["email"]}, "accessToken": token, "refreshToken": None}

    def refresh(self, refresh_token: str) -> Mapping[str, Any]:
        raise InsForgeError("SESSION_EXPIRED", "la sesión expiró; vuelve a entrar", status=401, kind=FailureKind.AUTH)

    def rpc(self, token: str, fn: str, params: Mapping[str, Any] | None = None) -> Any:
        with self._lock:
            entry = self._tokens.get(token)
        if entry is None or self.clock() > entry[1]:
            raise InsForgeError("SESSION_EXPIRED", "la sesión expiró; vuelve a entrar", status=401, kind=FailureKind.AUTH)
        return PgClient(self.pool, entry[0]).call(fn, params)
