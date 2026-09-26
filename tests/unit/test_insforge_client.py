"""Cliente de InsForge: sesión de servicio, renovación y mapeo de errores."""

import base64
import json
import time

import httpx
import pytest

from botcentro.insforge.client import ActivationRejected, InsForgeClient, NotFound, PermissionDenied


def _jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp, "sub": "svc"}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


class FakeInsForge:
    def __init__(self) -> None:
        self.logins = 0
        self.refreshes = 0
        self.rpc_tokens: list[str] = []
        self.expire_next = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/sessions":
            assert request.url.params["client_type"] == "server"
            self.logins += 1
            return httpx.Response(200, json={"accessToken": _jwt(time.time() + 900), "refreshToken": "r1"})
        if path == "/api/auth/refresh":
            self.refreshes += 1
            return httpx.Response(200, json={"accessToken": _jwt(time.time() + 900), "refreshToken": "r2"})
        if path.startswith("/api/database/rpc/"):
            self.rpc_tokens.append(request.headers["authorization"])
            if self.expire_next:
                self.expire_next = False
                return httpx.Response(401, json={"message": "jwt expired"})
            fn = path.rsplit("/", 1)[1]
            body = json.loads(request.content)
            if fn == "denied":
                return httpx.Response(403, json={"code": "42501", "message": "permiso insuficiente"})
            if fn == "activate":
                return httpx.Response(400, json={"code": "BC002", "message": "activación rechazada",
                                                 "details": "sin perfil de uso asociado"})
            if fn == "missing":
                return httpx.Response(404, json={"code": "P0002", "message": "fuente inexistente"})
            return httpx.Response(200, json=[{"echo": body}])
        return httpx.Response(404)


@pytest.fixture()
def fake() -> FakeInsForge:
    return FakeInsForge()


def _client(fake: FakeInsForge) -> InsForgeClient:
    return InsForgeClient("https://x.us-east.insforge.app", "svc@botcentro", "pw", transport=httpx.MockTransport(fake))


def test_rpc_logs_in_once_and_serializes_params(fake: FakeInsForge) -> None:
    client = _client(fake)
    import uuid
    from datetime import date
    from decimal import Decimal

    ident = uuid.uuid4()
    result = client.call("echo", {"p_id": ident, "p_day": date(2026, 9, 26), "p_cost": Decimal("0.10")})
    client.call("echo", {})
    assert result == [{"echo": {"p_id": str(ident), "p_day": "2026-09-26", "p_cost": "0.10"}}]
    assert fake.logins == 1 and all(t.startswith("Bearer h.") for t in fake.rpc_tokens)


def test_expired_token_is_refreshed_and_request_retried(fake: FakeInsForge) -> None:
    client = _client(fake)
    client.call("echo", {})
    fake.expire_next = True
    assert client.call("echo", {"a": 1}) == [{"echo": {"a": 1}}]
    assert fake.refreshes == 1 and fake.logins == 1


@pytest.mark.parametrize(("fn", "error"), [("denied", PermissionDenied), ("activate", ActivationRejected),
                                           ("missing", NotFound)])
def test_sqlstate_errors_are_typed(fake: FakeInsForge, fn: str, error: type) -> None:
    with pytest.raises(error) as info:
        _client(fake).call(fn, {})
    if fn == "activate":
        assert info.value.detail == "sin perfil de uso asociado"
