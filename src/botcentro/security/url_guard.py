"""Validación de URLs de adquisición contra SSRF (SRS-N04, T-25).

Capas:
  1. Sintaxis y política: esquema permitido, sin credenciales en la URL, host dentro de los
     dominios de la fuente, puerto permitido y ningún literal IP.
  2. Resolución: todas las IP resueltas deben ser públicas y enrutables (sin loopback, redes
     privadas, link-local, metadata de nube, CGNAT, multicast ni reservadas).
  3. Conexión: el transporte (botcentro.http.fetcher) resuelve y valida en el mismo paso en
     que conecta, y conecta a la IP validada, para impedir DNS rebinding. Cada redirección
     vuelve a pasar por la capa 1.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Sequence[str]]

# Redes no globales que ipaddress no marca de forma uniforme en todas las versiones.
_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "169.254.0.0/16",  # link-local, incluye 169.254.169.254 (metadata de nube)
        "192.0.0.0/24",
        "198.18.0.0/15",
        "fc00::/7",
        "fe80::/10",
        "64:ff9b::/96",  # NAT64: puede apuntar a IPv4 internas
    )
)


class UrlRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def system_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlRejected("DNS_FAILURE", f"no se pudo resolver {host}: {exc}") from exc
    return sorted({info[4][0] for info in infos})


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return is_public_address(str(mapped))
    if any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version):
        return False
    return ip.is_global and not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def normalize_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlRejected("INVALID_HOST", f"host inválido: {host!r}") from exc


@dataclass(frozen=True)
class UrlPolicy:
    """Política de una fuente (SRS-F01: dominios permitidos)."""

    allowed_domains: frozenset[str]
    allow_http: bool = False
    allowed_ports: frozenset[int] = field(default_factory=lambda: frozenset({443}))

    @classmethod
    def for_domains(cls, domains: Iterable[str], *, allow_http: bool = False) -> UrlPolicy:
        ports = {443, 80} if allow_http else {443}
        return cls(frozenset(normalize_host(d) for d in domains), allow_http, frozenset(ports))

    def host_allowed(self, host: str) -> bool:
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int


class UrlGuard:
    def __init__(self, policy: UrlPolicy, resolver: Resolver = system_resolver) -> None:
        if not policy.allowed_domains:
            raise ValueError("una fuente sin dominios permitidos no puede descargar nada")
        self.policy = policy
        self._resolver = resolver

    def check_url(self, url: str) -> Target:
        """Capa 1: sintaxis y política, sin tocar la red."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise UrlRejected("INVALID_URL", f"URL inválida: {url!r}") from exc
        scheme = parts.scheme.lower()
        if scheme not in ({"https", "http"} if self.policy.allow_http else {"https"}):
            raise UrlRejected("SCHEME_NOT_ALLOWED", f"esquema no permitido: {scheme or '(vacío)'}")
        if parts.username is not None or parts.password is not None:
            raise UrlRejected("CREDENTIALS_IN_URL", "la URL no puede incluir credenciales")
        if not parts.hostname:
            raise UrlRejected("INVALID_HOST", "la URL no tiene host")
        host = normalize_host(parts.hostname)
        try:
            ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            pass
        else:
            raise UrlRejected("IP_LITERAL", "no se admiten direcciones IP literales")
        if not self.policy.host_allowed(host):
            raise UrlRejected("DOMAIN_NOT_ALLOWED", f"dominio fuera de la fuente: {host}")
        effective_port = port if port is not None else (443 if scheme == "https" else 80)
        if effective_port not in self.policy.allowed_ports:
            raise UrlRejected("PORT_NOT_ALLOWED", f"puerto no permitido: {effective_port}")
        return Target(url=url, scheme=scheme, host=host, port=effective_port)

    def resolve_public(self, host: str, port: int) -> list[str]:
        """Capa 2: todas las direcciones resueltas deben ser públicas."""
        host = normalize_host(host)
        if not self.policy.host_allowed(host):
            raise UrlRejected("DOMAIN_NOT_ALLOWED", f"dominio fuera de la fuente: {host}")
        addresses = list(self._resolver(host, port))
        if not addresses:
            raise UrlRejected("DNS_FAILURE", f"{host} no resolvió a ninguna dirección")
        blocked = [a for a in addresses if not is_public_address(a)]
        if blocked:
            raise UrlRejected("PRIVATE_ADDRESS", f"{host} resuelve a direcciones no públicas: {', '.join(blocked)}")
        return addresses

    def validate(self, url: str) -> tuple[Target, list[str]]:
        target = self.check_url(url)
        return target, self.resolve_public(target.host, target.port)
