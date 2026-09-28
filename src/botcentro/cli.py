"""Herramientas de operación.

    python -m botcentro.cli verify-account ingest 123456
    python -m botcentro.cli ingest SRC-01 --from 2026-09-01 --to 2026-09-30

Lee la configuración de `.env` (ver .env.example). Las cuentas de servicio usan sus propias
credenciales; nunca la API key administrativa.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from botcentro.connectors.senado_open_data import SenadoOpenDataConnector
from botcentro.http.fetcher import SafeFetcher
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.insforge.client import InsForgeClient
from botcentro.security.url_guard import UrlGuard, UrlPolicy
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore

ACCOUNTS = {"ingest": "BOTCENTRO_INGEST", "query": "BOTCENTRO_QUERY"}
CONNECTORS = {"senado_open_data": SenadoOpenDataConnector}


def load_env(path: str = ".env") -> None:
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"falta {name} en .env")
    return value


def account_client(who: str) -> InsForgeClient:
    prefix = ACCOUNTS[who]
    return InsForgeClient(_required("BOTCENTRO_INSFORGE_URL"), _required(f"{prefix}_EMAIL"), _required(f"{prefix}_PASSWORD"))


def verify_account(who: str, code: str) -> None:
    prefix = ACCOUNTS[who]
    base = _required("BOTCENTRO_INSFORGE_URL")
    response = httpx.post(f"{base}/api/auth/email/verify", params={"client_type": "server"},
                          json={"email": _required(f"{prefix}_EMAIL"), "otp": code}, timeout=20)
    if response.status_code >= 400:
        sys.exit(f"verificación rechazada ({response.status_code}): {response.text[:200]}")
    client = account_client(who)
    roles = client.call("claim_pending_roles", {})
    caps = client.call("ops_capabilities", {})
    print(f"{who}: verificada; roles reclamados {roles}; roles actuales {caps['roles']}")


def _profile(policy: dict[str, Any]) -> UsageProfile:
    fields = ("capture_metadata", "download_files", "retain_content", "generate_derivatives", "index_content", "redistribute")
    return UsageProfile(**{f: Permission(policy[f]) for f in fields}, reviewed_at=policy.get("reviewed_at"),
                        reviewer=policy.get("reviewer"))


def ingest(code: str, start: str, end: str, window_days: int) -> None:
    client = account_client("ingest")
    source = client.select("sources", {"code": f"eq.{code}", "select": "*"})
    if not source:
        sys.exit(f"fuente {code} inexistente o no visible para la cuenta de ingesta")
    source = source[0]
    if source["state"] not in ("active", "degraded"):
        sys.exit(f"{code} está en estado {source['state']}: no admite ingesta productiva")
    policy = client.select("source_policies", {"id": f"eq.{source['policy_id']}", "select": "*"})[0]
    connector_cls = CONNECTORS.get(source["adapter"])
    if connector_cls is None:
        sys.exit(f"adaptador {source['adapter']} sin implementación")

    guard = UrlGuard(UrlPolicy.for_domains(source["allowed_domains"]))
    with SafeFetcher(guard) as fetcher:
        runner = IngestionRunner(
            source_id=source["id"],
            connector=connector_cls(fetcher),
            store=IngestStore(client),
            objects=LocalObjectStore(os.environ.get("BOTCENTRO_OBJECT_STORE_DIR", "var/objects")),
            profile=_profile(policy),
        )
        summary = runner.run(mode="backfill", scope={"from": start, "to": end, "window_days": window_days})
    print(json.dumps({k: str(v) if k == "run_id" else v for k, v in summary.__dict__.items()}, ensure_ascii=False, indent=1))


def main(argv: list[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="botcentro")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify-account", help="verifica una cuenta de servicio con su código de email")
    v.add_argument("who", choices=sorted(ACCOUNTS))
    v.add_argument("code")
    i = sub.add_parser("ingest", help="ejecuta una carga acotada de una fuente activa")
    i.add_argument("source")
    i.add_argument("--from", dest="start", required=True)
    i.add_argument("--to", dest="end", required=True)
    i.add_argument("--window-days", type=int, default=7)
    args = parser.parse_args(argv)
    if args.command == "verify-account":
        verify_account(args.who, args.code)
    else:
        ingest(args.source, args.start, args.end, args.window_days)


if __name__ == "__main__":
    main()
