"""BFF del panel: acceso por código, sesión en servidor, CSRF y lectura con la identidad del operador."""

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from botcentro.panel.server import create_panel_app
from botcentro.panel.sessions import InsForgeAuth, SessionStore


def _jwt(sub: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub, "exp": time.time() + 900}).encode()).rstrip(b"=").decode()
    return f"h.{payload}.s"


class FakeInsForge:
    def __init__(self) -> None:
        self.rpc_calls: list[tuple[str, str, dict]] = []
        self.codes_sent: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        if path == "/api/auth/email/send-otp":
            self.codes_sent.append(body["email"])
            return httpx.Response(202, json={"message": "ok"})
        if path == "/api/auth/sessions":
            if body.get("otp") != "123456":
                return httpx.Response(400, json={"message": "invalid code"})
            return httpx.Response(200, json={"user": {"id": "u-1", "email": body["email"]},
                                             "accessToken": _jwt("u-1"), "refreshToken": "r"})
        if path.startswith("/api/database/rpc/"):
            fn = path.rsplit("/", 1)[1]
            self.rpc_calls.append((fn, request.headers["authorization"], body))
            if fn == "claim_pending_roles":
                return httpx.Response(200, json=["admin"])
            return httpx.Response(200, json={"as_of": "2026-09-26T06:00:00Z", "access": True, "fn": fn, "params": body})
        return httpx.Response(404)


@pytest.fixture()
def fake() -> FakeInsForge:
    return FakeInsForge()


@pytest.fixture()
def client(fake: FakeInsForge, tmp_path) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html><title>panel</title>")
    store = SessionStore(InsForgeAuth("https://x.insforge.app", transport=httpx.MockTransport(fake)))
    return TestClient(create_panel_app(store, dist=tmp_path, secure_cookie=False))


CSRF = {"X-Botcentro-Panel": "1"}


def _login(client: TestClient) -> None:
    assert client.post("/v1/admin/auth/code", json={"email": "Dueno@Example.org"}, headers=CSRF).status_code == 202
    response = client.post("/v1/admin/auth/session", json={"email": "dueno@example.org", "code": "123456"}, headers=CSRF)
    assert response.status_code == 200 and response.json()["claimed_roles"] == ["admin"]
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie


def test_reads_require_session(client: TestClient) -> None:
    response = client.get("/v1/admin/ops/overview")
    assert response.status_code == 401 and response.json()["code"] == "SESSION_EXPIRED"


def test_mutations_require_csrf_header(client: TestClient, fake: FakeInsForge) -> None:
    response = client.post("/v1/admin/auth/code", json={"email": "a@b.co"})
    assert response.status_code == 403 and not fake.codes_sent


def test_login_claims_roles_and_reads_with_user_token(client: TestClient, fake: FakeInsForge) -> None:
    _login(client)
    assert fake.codes_sent == ["dueno@example.org"]
    data = client.get("/v1/admin/ops/jobs?state=dead_letter&limit=500&ignored=1").json()
    assert data["fn"] == "ops_jobs" and data["params"] == {"p_state": "dead_letter", "p_limit": 100}
    _, auth, _ = fake.rpc_calls[-1]
    payload = auth.removeprefix("Bearer ").split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["sub"] == "u-1"  # lee con el token del operador, nunca con una API key


def test_wrong_code_is_rejected_without_session(client: TestClient) -> None:
    response = client.post("/v1/admin/auth/session", json={"email": "a@b.co", "code": "000000"}, headers=CSRF)
    assert response.status_code == 401 and "set-cookie" not in response.headers


def test_invalid_filters_and_unknown_views(client: TestClient) -> None:
    _login(client)
    assert client.get("/v1/admin/ops/jobs?state=drop table").status_code == 422
    assert client.get("/v1/admin/ops/secrets").status_code == 404


def test_code_requests_are_rate_limited(client: TestClient) -> None:
    statuses = [client.post("/v1/admin/auth/code", json={"email": "x@y.co"}, headers=CSRF).status_code for _ in range(6)]
    assert statuses == [202] * 5 + [429]


def test_logout_drops_session(client: TestClient) -> None:
    _login(client)
    assert client.post("/v1/admin/auth/logout", json={}, headers=CSRF).status_code == 200
    assert client.get("/v1/admin/ops/overview").status_code == 401


def test_security_headers_and_spa_fallback(client: TestClient) -> None:
    response = client.get("/fuentes/abc")
    assert response.status_code == 200 and "panel" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
