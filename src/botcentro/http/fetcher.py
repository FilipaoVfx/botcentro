"""Descarga segura de recursos de fuentes (SRS-F06, SRS-N04, §8 paso 1; T-13, T-25).

* Conexión solo a IP públicas validadas en el momento de conectar (anti DNS rebinding).
* Redirecciones manuales, cada una validada contra la política de la fuente.
* Límite de tamaño aplicado al cuerpo descomprimido: superarlo es RESOURCE_LIMIT, nunca
  truncamiento silencioso.
* Tipo real por firma del contenido; la extensión o el Content-Type declarado no bastan.
* Sin proxies de entorno (trust_env=False): un proxy saltaría la validación de destino.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpcore
import httpx

from botcentro.connectors.retry import classify_http_status, parse_retry_after
from botcentro.domain.hashing import sha256_hex
from botcentro.errors import FailureKind, FetchError
from botcentro.security.url_guard import UrlGuard, UrlRejected

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
DEFAULT_USER_AGENT = "botcentro-legislativo/0.1 (consulta legislativa; contacto en configuracion)"


@dataclass(frozen=True)
class FetchLimits:
    """Valores iniciales de SRS §8 (50 MB, 10 min por etapa); ajustables con muestras reales."""

    max_bytes: int = 50 * 1024 * 1024
    timeout_seconds: float = 60.0
    max_redirects: int = 5


@dataclass(frozen=True)
class Fetched:
    requested_url: str
    final_url: str
    status: int
    content: bytes
    content_hash: str
    declared_mime: str | None
    sniffed_mime: str | None
    etag: str | None
    last_modified: str | None
    fetched_at: datetime

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class NotModified:
    requested_url: str
    final_url: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime
    status: int = 304


class GuardedBackend(httpcore.NetworkBackend):
    """Resuelve, valida y conecta a una IP pública en un único paso."""

    def __init__(self, guard: UrlGuard, inner: httpcore.NetworkBackend | None = None) -> None:
        self._guard = guard
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = self._guard.resolve_public(host, port)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        raise UrlRejected("UNIX_SOCKET", "no se permiten sockets unix")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class GuardedTransport(httpx.HTTPTransport):
    """Transporte httpx cuyo pool usa GuardedBackend (ver prueba test_transport_uses_guard)."""

    def __init__(self, guard: UrlGuard, *, max_connections: int = 4) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=max_connections,
            network_backend=GuardedBackend(guard),
        )


def sniff_mime(content: bytes, declared: str | None = None) -> str | None:
    """Tipo por firma. CSV solo se acepta si se declara y el contenido es texto."""
    head = content[:1024]
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped[:1] in (b"{", b"["):
        try:
            json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            pass
        else:
            return "application/json"
    lowered = stripped[:256].lower()
    if lowered.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if lowered.startswith(b"<?xml"):
        return "application/xml"
    if b"\x00" not in content[:8192]:
        base = (declared or "").split(";", 1)[0].strip().lower()
        return "text/csv" if base in {"text/csv", "application/csv"} else "text/plain"
    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SafeFetcher:
    def __init__(
        self,
        guard: UrlGuard,
        *,
        limits: FetchLimits = FetchLimits(),
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._guard = guard
        self._limits = limits
        self._clock = clock
        self._client = httpx.Client(
            transport=transport or GuardedTransport(guard),
            follow_redirects=False,
            timeout=limits.timeout_seconds,
            headers={"User-Agent": user_agent},
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def clear_cookies(self) -> None:
        """Descarta la sesión HTTP (cookies) antes de un flujo que debe empezar limpio."""
        self._client.cookies.clear()

    def __enter__(self) -> SafeFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        accept_mimes: frozenset[str] | None = None,
        method: str = "GET",
        data: Mapping[str, str] | None = None,
    ) -> Fetched | NotModified:
        """GET (por defecto) o POST de formulario. Tras 301/302/303 un POST continúa como GET."""
        if method not in ("GET", "POST"):
            raise ValueError("solo se admiten GET y POST")
        try:
            return self._fetch(url, etag, last_modified, accept_mimes, method, data)
        except UrlRejected as exc:
            raise FetchError(exc.code, str(exc), kind=FailureKind.POLICY) from exc
        except httpx.TimeoutException as exc:
            raise FetchError("TIMEOUT", f"tiempo de espera agotado: {url}", kind=FailureKind.TRANSIENT) from exc
        except httpx.TransportError as exc:
            raise FetchError("NETWORK_ERROR", f"error de red: {exc}", kind=FailureKind.TRANSIENT) from exc

    def _fetch(
        self, url: str, etag: str | None, last_modified: str | None, accept_mimes: frozenset[str] | None,
        method: str = "GET", data: Mapping[str, str] | None = None,
    ) -> Fetched | NotModified:
        self._guard.check_url(url)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        current = url
        for _ in range(self._limits.max_redirects + 1):
            with self._client.stream(method, current, headers=headers,
                                     data=dict(data) if method == "POST" and data else None) as response:
                # httpx considera redirección cualquier 3xx (incluido 304): se usa la lista explícita.
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("REDIRECT_WITHOUT_LOCATION", "redirección sin destino", kind=FailureKind.INVALID_CONTENT)
                    current = urljoin(current, location)
                    self._guard.check_url(current)
                    if response.status_code in (301, 302, 303):
                        method, data = "GET", None
                    continue
                return self._read(url, current, response, accept_mimes)
        raise FetchError("TOO_MANY_REDIRECTS", f"más de {self._limits.max_redirects} redirecciones", kind=FailureKind.POLICY)

    def _read(
        self, requested: str, final: str, response: httpx.Response, accept_mimes: frozenset[str] | None
    ) -> Fetched | NotModified:
        now = self._clock()
        status = response.status_code
        resp_etag = response.headers.get("etag")
        resp_last_modified = response.headers.get("last-modified")
        if status == 304:
            return NotModified(requested, final, resp_etag, resp_last_modified, now)
        if status >= 400:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) >= 4096:
                    break
            raise FetchError(
                f"HTTP_{status}",
                f"respuesta {status} de {final}",
                kind=classify_http_status(status),
                retry_after=parse_retry_after(response.headers.get("retry-after"), now),
                status=status,
                body=bytes(body[:4096]),
            )
        if status != 200:
            raise FetchError(f"HTTP_{status}", f"estado inesperado {status}", kind=FailureKind.INVALID_CONTENT, status=status)

        declared_length = response.headers.get("content-length")
        if declared_length and declared_length.isdigit() and int(declared_length) > self._limits.max_bytes:
            raise FetchError("RESOURCE_LIMIT", f"el recurso declara {declared_length} bytes", kind=FailureKind.RESOURCE_LIMIT)

        buffer = bytearray()
        for chunk in response.iter_bytes():
            buffer.extend(chunk)
            if len(buffer) > self._limits.max_bytes:
                raise FetchError("RESOURCE_LIMIT", f"el recurso supera {self._limits.max_bytes} bytes", kind=FailureKind.RESOURCE_LIMIT)
        content = bytes(buffer)

        declared_mime = response.headers.get("content-type")
        sniffed = sniff_mime(content, declared_mime)
        if accept_mimes is not None and sniffed not in accept_mimes:
            raise FetchError(
                "UNEXPECTED_CONTENT_TYPE",
                f"contenido {sniffed or 'desconocido'} (declarado {declared_mime or '-'}) no esperado",
                kind=FailureKind.INVALID_CONTENT,
            )
        return Fetched(
            requested_url=requested,
            final_url=final,
            status=status,
            content=content,
            content_hash=sha256_hex(content),
            declared_mime=declared_mime,
            sniffed_mime=sniffed,
            etag=resp_etag,
            last_modified=resp_last_modified,
            fetched_at=now,
        )
