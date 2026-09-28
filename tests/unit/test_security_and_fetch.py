"""SSRF (SRS-N04, T-25), descarga segura (T-13) y política de reintentos (T-04)."""

import random
from datetime import datetime, timezone

import httpx
import pytest

from botcentro.connectors.retry import (
    Action,
    RetryPolicy,
    backoff_delay,
    classify_http_status,
    decide,
    parse_retry_after,
)
from botcentro.domain.hashing import sha256_hex
from botcentro.errors import FailureKind, FetchError
from botcentro.http.fetcher import FetchLimits, GuardedTransport, NotModified, SafeFetcher, sniff_mime
from botcentro.security.url_guard import UrlGuard, UrlPolicy, UrlRejected, is_public_address

POLICY = UrlPolicy.for_domains(["senado.gov.co"])
PUBLIC_IP = "190.24.134.10"


def guard(ips: dict[str, list[str]] | None = None) -> UrlGuard:
    table = ips or {}
    return UrlGuard(POLICY, resolver=lambda host, port: table.get(host, [PUBLIC_IP]))


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://leyes.senado.gov.co/x", "SCHEME_NOT_ALLOWED"),
        ("file:///etc/passwd", "SCHEME_NOT_ALLOWED"),
        ("https://user:pw@leyes.senado.gov.co/", "CREDENTIALS_IN_URL"),
        ("https://evil.com/senado.gov.co", "DOMAIN_NOT_ALLOWED"),
        ("https://senado.gov.co.evil.com/", "DOMAIN_NOT_ALLOWED"),
        ("https://169.254.169.254/latest/meta-data", "IP_LITERAL"),
        ("https://[::1]/", "IP_LITERAL"),
        ("https://leyes.senado.gov.co:8443/", "PORT_NOT_ALLOWED"),
    ],
)
def test_url_policy_rejections(url: str, code: str) -> None:
    with pytest.raises(UrlRejected) as info:
        guard().check_url(url)
    assert info.value.code == code


def test_subdomains_of_allowed_domain_pass() -> None:
    assert guard().check_url("https://leyes.senado.gov.co/proyectos").host == "leyes.senado.gov.co"


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
     "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1", "2002:7f00:1::"],
)
def test_non_public_addresses_are_blocked(address: str) -> None:
    assert not is_public_address(address)


def test_dns_resolving_to_private_address_is_rejected() -> None:
    with pytest.raises(UrlRejected) as info:
        guard({"intranet.senado.gov.co": ["10.0.0.8", PUBLIC_IP]}).validate("https://intranet.senado.gov.co/")
    assert info.value.code == "PRIVATE_ADDRESS"


def test_transport_uses_guard_at_connect_time() -> None:
    # DNS rebinding: la URL es válida, pero en el momento de conectar resuelve a loopback.
    rebinding = guard({"leyes.senado.gov.co": ["127.0.0.1"]})
    with httpx.Client(transport=GuardedTransport(rebinding)) as client, pytest.raises(UrlRejected) as info:
        client.get("https://leyes.senado.gov.co/")
    assert info.value.code == "PRIVATE_ADDRESS"


def _fetcher(handler, limits: FetchLimits = FetchLimits()) -> SafeFetcher:
    return SafeFetcher(guard(), limits=limits, transport=httpx.MockTransport(handler),
                       clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc))


def test_redirects_are_validated_against_policy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        if request.url.path == "/b":
            return httpx.Response(301, headers={"location": "https://metadata.internal/latest"})
        return httpx.Response(200, content=b"%PDF-1.7")

    with pytest.raises(FetchError) as info:
        _fetcher(handler).fetch("https://leyes.senado.gov.co/a")
    assert info.value.code == "DOMAIN_NOT_ALLOWED" and info.value.kind is FailureKind.POLICY


def test_successful_fetch_hashes_and_sniffs_real_type() -> None:
    body = b"%PDF-1.7\n..."
    fetched = _fetcher(lambda r: httpx.Response(200, content=body, headers={"content-type": "text/html", "etag": '"v1"'})) \
        .fetch("https://leyes.senado.gov.co/doc.html", accept_mimes=frozenset({"application/pdf"}))
    assert fetched.sniffed_mime == "application/pdf" and fetched.etag == '"v1"'
    assert fetched.content_hash == sha256_hex(body) and fetched.byte_size == len(body)


def test_unexpected_content_type_is_rejected_despite_extension() -> None:
    with pytest.raises(FetchError) as info:
        _fetcher(lambda r: httpx.Response(200, content=b"<html>login</html>")) \
            .fetch("https://leyes.senado.gov.co/gaceta.pdf", accept_mimes=frozenset({"application/pdf"}))
    assert info.value.code == "UNEXPECTED_CONTENT_TYPE"


def test_size_limit_is_an_error_not_truncation() -> None:
    fetcher = _fetcher(lambda r: httpx.Response(200, content=b"x" * 2048), FetchLimits(max_bytes=1024))
    with pytest.raises(FetchError) as info:
        fetcher.fetch("https://leyes.senado.gov.co/big")
    assert info.value.kind is FailureKind.RESOURCE_LIMIT


def test_conditional_requests_and_not_modified() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304, headers={"etag": '"v1"'})

    result = _fetcher(handler).fetch("https://leyes.senado.gov.co/x", etag='"v1"')
    assert isinstance(result, NotModified) and seen["if-none-match"] == '"v1"'


def test_http_errors_are_classified_with_retry_after() -> None:
    with pytest.raises(FetchError) as info:
        _fetcher(lambda r: httpx.Response(429, headers={"retry-after": "120"})).fetch("https://leyes.senado.gov.co/x")
    assert info.value.kind is FailureKind.RATE_LIMITED and info.value.retry_after == 120


def test_sniff_mime() -> None:
    assert sniff_mime(b'{"a": 1}') == "application/json"
    assert sniff_mime(b"<!DOCTYPE html><html>") == "text/html"
    assert sniff_mime(b"a,b\n1,2", "text/csv; charset=utf-8") == "text/csv"
    assert sniff_mime(b"\x00\x01binary") is None


@pytest.mark.parametrize(
    ("status", "kind"),
    [(429, FailureKind.RATE_LIMITED), (503, FailureKind.TRANSIENT), (401, FailureKind.AUTH),
     (403, FailureKind.PERMISSION), (404, FailureKind.NOT_FOUND), (400, FailureKind.INVALID_CONTENT)],
)
def test_classify_http_status(status: int, kind: FailureKind) -> None:
    assert classify_http_status(status) is kind


def test_backoff_grows_and_is_capped() -> None:
    rng, policy = random.Random(1), RetryPolicy()
    delays = [backoff_delay(n, policy, rng) for n in range(1, 10)]
    assert 15 <= delays[0] <= 30
    assert all(d <= policy.max_delay for d in delays)
    assert delays[-1] >= policy.max_delay / 2


def test_decisions() -> None:
    assert decide(FailureKind.TRANSIENT, 1, retry_after=600).delay_seconds >= 600
    assert decide(FailureKind.TRANSIENT, 5).action is Action.DEAD_LETTER
    assert decide(FailureKind.AUTH, 1).action is Action.SUSPEND_SOURCE
    assert decide(FailureKind.SCHEMA_CHANGED, 1).action is Action.QUARANTINE
    assert decide(FailureKind.NOT_FOUND, 1).action is Action.SKIP


def test_parse_retry_after() -> None:
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    assert parse_retry_after("30", now) == 30
    assert parse_retry_after("Sat, 26 Sep 2026 12:01:00 GMT", now) == 60
    assert parse_retry_after("mañana", now) is None


def test_post_form_and_redirect_switches_to_get() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        if request.url.path == "/ajax":
            return httpx.Response(303, headers={"location": "/result"})
        return httpx.Response(200, content=b'{"ok": true}')

    result = _fetcher(handler).fetch("https://leyes.senado.gov.co/ajax", method="POST", data={"action": "x"})
    assert result.sniffed_mime == "application/json"
    assert seen[0][0] == "POST" and b"action=x" in seen[0][2]
    assert seen[1][:2] == ("GET", "/result")
