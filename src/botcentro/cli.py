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


def _resumable_cursor(client: InsForgeClient, source_id: str, version: str, scope: dict[str, Any]) -> dict[str, Any] | None:
    """Último cursor confirmado de una ejecución no completada con el mismo alcance y versión."""
    runs = client.select("ingestion_runs", {
        "source_id": f"eq.{source_id}", "select": "status,cursor_after", "order": "started_at.desc", "limit": "5",
    })
    for run in runs:
        cursor = run.get("cursor_after")
        if run["status"] == "succeeded":
            return None
        if cursor and cursor.get("connector_version") == version and cursor.get("scope") == scope:
            return cursor
    return None


def ingest(code: str, start: str, end: str, window_days: int, reparse: bool = False) -> None:
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
            reparse=reparse,
        )
        scope = {"from": start, "to": end, "window_days": window_days}
        previous = _resumable_cursor(client, source["id"], connector_cls.version, scope)
        if previous:
            print(f"reanudando desde {previous['position']}", file=sys.stderr)
        summary = runner.run(mode="backfill", scope=scope, previous_cursor=previous)
    print(json.dumps({k: str(v) if k == "run_id" else v for k, v in summary.__dict__.items()}, ensure_ascii=False, indent=1))


# InsForge limita cada RPC a 10 s: los pasos por lotes se repiten hasta que no queda nada pendiente.
NORMALIZE_STEPS = ("catalog", "projects", "sessions", "votings", "vote_observations", "current_votes",
                   "attendance", "agenda")
BATCHED_STEPS = {"vote_observations", "current_votes", "attendance"}


def normalize(code: str, batch: int = 20000) -> None:
    if code != "SRC-01":
        sys.exit("solo SRC-01 tiene normalizador por ahora")
    client = account_client("ingest")
    for step in NORMALIZE_STEPS:
        total: dict[str, int] = {}
        while True:
            result = client.call("normalize_senado_od", {"p_step": step, "p_limit": batch})
            counts = {k: v for k, v in result.items() if k != "step"}
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            if step not in BATCHED_STEPS or not any(counts.values()):
                break
        print(json.dumps({"step": step, **total}, ensure_ascii=False), flush=True)


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
    i.add_argument("--reparse", action="store_true", help="reinterpreta capturas sin cambios (nuevo parser)")
    n = sub.add_parser("normalize", help="convierte observaciones publicadas en entidades (idempotente)")
    n.add_argument("source")
    args = parser.parse_args(argv)
    if args.command == "verify-account":
        verify_account(args.who, args.code)
    elif args.command == "normalize":
        normalize(args.source)
    else:
        ingest(args.source, args.start, args.end, args.window_days, args.reparse)


if __name__ == "__main__":
    main()
