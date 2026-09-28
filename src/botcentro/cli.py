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
from collections.abc import Sequence
from typing import Any

import httpx

from botcentro.connectors.camara_proyectos import CamaraProyectosConnector
from botcentro.connectors.senado_open_data import SenadoOpenDataConnector
from botcentro.http.fetcher import SafeFetcher
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.insforge.client import InsForgeClient
from botcentro.security.url_guard import UrlGuard, UrlPolicy
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore

ACCOUNTS = {"ingest": "BOTCENTRO_INGEST", "query": "BOTCENTRO_QUERY"}
CONNECTORS = {"senado_open_data": SenadoOpenDataConnector, "camara_proyectos": CamaraProyectosConnector}


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


CAMARA_STEPS = ("projects", "status", "authors")


def _run_steps(client: InsForgeClient, fn: str, steps: Sequence[str], batched: set[str], batch: int) -> None:
    for step in steps:
        total: dict[str, int] = {}
        while True:
            result = client.call(fn, {"p_step": step, "p_limit": batch})
            counts = {k: v for k, v in result.items() if k != "step"}
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v
            if step not in batched or not any(counts.values()):
                break
        print(json.dumps({"step": step, **total}, ensure_ascii=False), flush=True)


def normalize(code: str) -> None:
    client = account_client("ingest")
    if code == "SRC-01":
        _run_steps(client, "normalize_senado_od", NORMALIZE_STEPS, BATCHED_STEPS, 20000)
    elif code == "SRC-06":
        _run_steps(client, "normalize_camara_pl", CAMARA_STEPS, set(CAMARA_STEPS), 100)
    else:
        sys.exit(f"{code} no tiene normalizador")


def qdrant_store():
    from botcentro.vectors.qdrant import QdrantStore

    store = QdrantStore(_required("BOTCENTRO_QDRANT_URL"), _required("BOTCENTRO_QDRANT_API_KEY"))
    store.ensure_collection()
    return store


def sync_qdrant(batch: int) -> None:
    from botcentro.embeddings.e5 import E5SmallEmbedder
    from botcentro.vectors.sync import as_chunk, sync_chunks

    embedder = E5SmallEmbedder()
    store = qdrant_store()
    totals = sync_chunks(account_client("ingest"), store, batch=batch,
                         embed_missing=lambda rows: embedder.embed_passages([as_chunk(r) for r in rows]),
                         progress=lambda t: print(json.dumps(t), flush=True))
    print(json.dumps({**totals, "points_in_collection": store.count()}), flush=True)


def migrate_qdrant(skip_kinds: str, source: str) -> None:
    """Copia puntos a la colección activa: desde la colección anterior (`legacy`) o desde Qdrant
    Cloud (`cloud`, DEC-15: el índice pasa a Qdrant autoalojado en el servidor del bot)."""
    from botcentro.vectors.qdrant import COLLECTION, LEGACY_COLLECTION, QdrantStore
    from botcentro.vectors.sync import migrate_collection

    target = qdrant_store()
    if source == "cloud":
        origin = QdrantStore(_required("BOTCENTRO_QDRANT_CLOUD_URL"), _required("BOTCENTRO_QDRANT_CLOUD_API_KEY"),
                             collection=COLLECTION)
    else:
        origin = QdrantStore(_required("BOTCENTRO_QDRANT_URL"), _required("BOTCENTRO_QDRANT_API_KEY"),
                             collection=LEGACY_COLLECTION)
    totals = migrate_collection(origin, target, skip_segment_kinds=[k for k in skip_kinds.split(",") if k],
                                progress=lambda t: print(json.dumps(t), flush=True))
    print(json.dumps({**totals, "target_points": target.count(), "source_points": origin.count()}), flush=True)


def load_gacetas(since: str, skip_kinds: str, out: str, max_points: int, max_stored_mb: int) -> None:
    from datetime import date
    from pathlib import Path

    from botcentro.connectors.gacetas_imprenta import DOMAINS
    from botcentro.costs.budget import BudgetGuard
    from botcentro.embeddings.e5 import E5SmallEmbedder
    from botcentro.http.fetcher import FetchLimits
    from botcentro.pilots.gacetas import run_load

    client = account_client("ingest")
    fetcher = SafeFetcher(UrlGuard(UrlPolicy.for_domains(DOMAINS)),
                          limits=FetchLimits(max_bytes=250 * 1024 * 1024, timeout_seconds=300))
    totals = run_load(out=Path(out), fetcher=fetcher, client=client, store=qdrant_store(), embedder=E5SmallEmbedder(),
                      budget=BudgetGuard(client), since=date.fromisoformat(since),
                      skip_kinds=[k for k in skip_kinds.split(",") if k], max_points=max_points,
                      max_stored_bytes=max_stored_mb * 1024 * 1024)
    print(json.dumps(totals), flush=True)


def run_bot() -> None:
    """Bot de Telegram por sondeo largo (DEC-16): recibe, responde sin IA y registra entregas."""
    import logging
    import socket

    from botcentro.config import _key
    from botcentro.embeddings.e5 import E5SmallEmbedder
    from botcentro.query.engine import AnswerEngine
    from botcentro.telegram.bot import BotService
    from botcentro.telegram.client import BotApi
    from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx registra cada URL en INFO y las de la Bot API contienen el token: nunca deben llegar al log.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    api = BotApi(_required("BOTCENTRO_TELEGRAM_BOT_TOKEN"))
    me = api.get_me()
    pseudonym_key = _key(os.environ, "BOTCENTRO_PSEUDONYM_KEY")
    if pseudonym_key is None:
        sys.exit("falta BOTCENTRO_PSEUDONYM_KEY en .env")
    client = account_client("query")
    settings = WebhookSettings(api.bot_id, "sondeo-largo", pseudonym_key,
                               int(os.environ.get("BOTCENTRO_TELEGRAM_RATE_LIMIT", "10")))
    embedder = E5SmallEmbedder()
    engine = AnswerEngine(client, qdrant_store(), embedder.embed_query)
    print(json.dumps({"bot": me.get("username"), "bot_id": api.bot_id}), flush=True)
    BotService(api, TelegramWebhook(settings, client), client, engine,
               worker_id=f"bot-{socket.gethostname()}",
               message_limit=int(os.environ.get("BOTCENTRO_TELEGRAM_MESSAGE_LIMIT", "4096"))).run_forever()


def pilot_gacetas(limit: int, out: str) -> None:
    from pathlib import Path

    from botcentro.connectors.gacetas_imprenta import DOMAINS
    from botcentro.costs.budget import BudgetGuard
    from botcentro.embeddings.e5 import E5SmallEmbedder
    from botcentro.http.fetcher import FetchLimits
    from botcentro.pilots.gacetas import run_pilot

    client = account_client("ingest")
    fetcher = SafeFetcher(UrlGuard(UrlPolicy.for_domains(DOMAINS)),
                          limits=FetchLimits(max_bytes=120 * 1024 * 1024, timeout_seconds=180))
    totals = run_pilot(limit=limit, out=Path(out), fetcher=fetcher, client=client, store=qdrant_store(),
                       embedder=E5SmallEmbedder(), budget=BudgetGuard(client))
    print(json.dumps(totals), flush=True)


def index_fichas(batch: int) -> None:
    from botcentro.costs.budget import BudgetGuard
    from botcentro.documents.fichas import FichaIndexer
    from botcentro.embeddings.e5 import E5SmallEmbedder

    client = account_client("ingest")
    indexer = FichaIndexer(client, E5SmallEmbedder(), BudgetGuard(client), qdrant_store(), batch=batch)
    totals: dict[str, int] = {}
    while (counts := indexer.run_batch()) is not None:
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        print(json.dumps(totals, ensure_ascii=False), flush=True)
        if not counts["created"] and not counts["revised"]:
            break  # solo quedan fichas sin texto: no se reintentan en bucle


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
    f = sub.add_parser("index-fichas", help="documenta e indexa (e5-small) las fichas de proyecto de SRC-06")
    f.add_argument("--batch", type=int, default=100)
    m = sub.add_parser("migrate-qdrant", help="copia la colección anterior a la v2 (float16, carga mínima)")
    m.add_argument("--skip-kinds", default="")
    m.add_argument("--source", choices=("legacy", "cloud"), default="cloud")
    lg = sub.add_parser("load-gacetas", help="carga SRC-03 desde una fecha → OCR → Qdrant (reanudable, con topes)")
    lg.add_argument("--since", required=True)
    lg.add_argument("--skip-kinds", default="portada,otro")
    lg.add_argument("--out", default="var/gacetas-manifest.jsonl")
    lg.add_argument("--max-points", type=int, default=950_000)
    lg.add_argument("--max-stored-mb", type=int, default=2_900)
    q = sub.add_parser("sync-qdrant", help="publica en Qdrant los chunks registrados (idempotente)")
    q.add_argument("--batch", type=int, default=500)
    g = sub.add_parser("pilot-gacetas", help="piloto SRC-03: gacetas recientes → OCR → Qdrant, con métricas")
    g.add_argument("--limit", type=int, default=200)
    g.add_argument("--out", default="var/pilot-gacetas.jsonl")
    sub.add_parser("bot", help="ejecuta el bot de Telegram (sondeo largo, respuestas sin IA)")
    args = parser.parse_args(argv)
    if args.command == "verify-account":
        verify_account(args.who, args.code)
    elif args.command == "normalize":
        normalize(args.source)
    elif args.command == "bot":
        run_bot()
    elif args.command == "migrate-qdrant":
        migrate_qdrant(args.skip_kinds, args.source)
    elif args.command == "load-gacetas":
        load_gacetas(args.since, args.skip_kinds, args.out, args.max_points, args.max_stored_mb)
    elif args.command == "pilot-gacetas":
        pilot_gacetas(args.limit, args.out)
    elif args.command == "sync-qdrant":
        sync_qdrant(args.batch)
    elif args.command == "index-fichas":
        index_fichas(args.batch)
    else:
        ingest(args.source, args.start, args.end, args.window_days, args.reparse)


if __name__ == "__main__":
    main()
