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
import time
from pathlib import Path
from collections.abc import Sequence
from typing import Any

import httpx

from botcentro.connectors.camara_proyectos import CamaraProyectosConnector
from botcentro.connectors.senado_open_data import SenadoOpenDataConnector
from botcentro.connectors.youtube_congreso import YoutubeCongresoConnector
from botcentro.http.fetcher import SafeFetcher
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.insforge.client import InsForgeClient
from botcentro.security.url_guard import UrlGuard, UrlPolicy
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore

ACCOUNTS = {"ingest": "BOTCENTRO_INGEST", "query": "BOTCENTRO_QUERY"}
CONNECTORS = {"senado_open_data": SenadoOpenDataConnector, "camara_proyectos": CamaraProyectosConnector,
              "youtube_congreso": YoutubeCongresoConnector}


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
    """Cuenta de servicio. Con BOTCENTRO_DATABASE_URL usa el PostgreSQL autoalojado (DEC-21)."""
    prefix = ACCOUNTS[who]
    if os.environ.get("BOTCENTRO_DATABASE_URL"):
        from botcentro.db.pgclient import service_client

        return service_client(_required(f"{prefix}_EMAIL"))  # type: ignore[return-value]
    return InsForgeClient(_required("BOTCENTRO_INSFORGE_URL"), _required(f"{prefix}_EMAIL"), _required(f"{prefix}_PASSWORD"))


def muestra_secop(per_territory: int, seed: int) -> None:
    """Muestra de verificación de SECOP II antes de publicarlo (§24.6). Escribe en var/ (fuera del repo)."""
    from datetime import date

    from psycopg.rows import dict_row

    import psycopg
    from botcentro.investigations.muestra import SAMPLE_SQL, SampleItem, choose, compare, stored_view, write_report
    from botcentro.investigations.socrata import DOMAINS, SocrataClient

    with psycopg.connect(_required("BOTCENTRO_DATABASE_ADMIN_URL"), row_factory=dict_row) as conn:
        rows = conn.execute(SAMPLE_SQL, {"code": "SRC-15"}).fetchall()
    items = [SampleItem(reason, r["native_id"], r["territory"] or "sin territorio", stored_view(r))
             for reason, r in choose(rows, per_territory=per_territory, seed=seed)]
    key = _required("BOTCENTRO_IDENTITY_KEY").encode()
    with SafeFetcher(UrlGuard(UrlPolicy.for_domains(DOMAINS))) as fetcher:
        compare(items, SocrataClient(fetcher), key)
    md, csv_path = write_report(items, Path("var"), today=date.today())
    same = sum(1 for i in items if i.source and not i.diffs)
    print(json.dumps({"muestra": len(items), "coinciden": same, "con_diferencias": sum(1 for i in items if i.diffs),
                      "sin_consulta": sum(1 for i in items if i.fetch_error), "markdown": str(md), "csv": str(csv_path)},
                     ensure_ascii=False))


def index_actas(manifest: str, backfill: int, pause: float) -> None:
    """Publica las actas registradas por la carga de gacetas. Con --backfill N, recupera hasta N gacetas
    cargadas antes de registrar actas (solo las que tienen segmento «acta»), sin conservar el PDF."""
    import time as _time
    from datetime import date as _date
    from urllib.parse import parse_qs, urlparse

    from botcentro.connectors.gacetas_imprenta import DOMAINS as GACETA_DOMAINS, GacetaRef
    from botcentro.pilots.gacetas import acta_headers_only

    client = account_client("ingest")
    path, extra = Path(manifest), Path(manifest).with_suffix(".actas.jsonl")
    seen: dict[str, dict[str, Any]] = {}
    for file in (path, extra):
        if file.exists():
            for line in file.read_text().splitlines():
                rec = json.loads(line)
                if "actas" in rec or rec["document_key"] not in seen:
                    seen[rec["document_key"]] = rec
    todo = [r for r in seen.values() if "actas" not in r and "acta" in (r.get("segment_kinds") or [])][:backfill]
    if todo:
        with SafeFetcher(UrlGuard(UrlPolicy.for_domains(GACETA_DOMAINS))) as fetcher, extra.open("a") as sink:
            for rec in todo:
                _, corp, _, number = rec["document_key"].split(":")
                d, m, y = (int(x) for x in parse_qs(urlparse(rec["url"]).query)["fec"][0].split("-"))
                try:
                    out = acta_headers_only(GacetaRef(corp, _date(y, m, d), number), fetcher=fetcher)
                except Exception as exc:  # noqa: BLE001 — se registra y se sigue; se reintenta en otra pasada
                    print(json.dumps({"document_key": rec["document_key"], "error": f"{type(exc).__name__}: {exc}"[:200]}),
                          flush=True)
                    continue
                sink.write(json.dumps({**rec, **out}, ensure_ascii=False) + "\n")
                sink.flush()
                seen[rec["document_key"]] = {**rec, **out}
                print(json.dumps({"document_key": rec["document_key"], "actas": len(out["actas"])}), flush=True)
                _time.sleep(pause)
    from botcentro.documents.acta_headers import parse_acta_headers

    rows = []
    for rec in seen.values():
        if rec.get("acta_contexts") is not None:  # se reinterpreta con el analizador vigente, sin descargar
            corp = rec["document_key"].split(":")[1]
            rec["actas"] = [h.as_dict() for h in parse_acta_headers([(p, t) for p, t in rec["acta_contexts"]], corp)]
        published = parse_qs(urlparse(rec["url"]).query).get("fec", [""])[0]
        d, m, y = (published.split("-") + ["", "", ""])[:3]
        for a in rec.get("actas") or []:
            rows.append({**a, "document_key": rec["document_key"], "gaceta_url": rec["url"],
                         "published_on": f"{y}-{int(m):02d}-{int(d):02d}" if y else None})
    total = sum(client.call("ingest_session_actas", {"p_rows": rows[i:i + 500]}) for i in range(0, len(rows), 500))
    print(json.dumps({"actas_publicadas": total, "gacetas_con_actas": sum(1 for r in seen.values() if r.get("actas")),
                      "pendientes_de_recuperar": sum(1 for r in seen.values()
                                                     if "actas" not in r and "acta" in (r.get("segment_kinds") or []))}))


def db_bootstrap() -> None:
    from botcentro.db.migrate import bootstrap

    bootstrap(_required("BOTCENTRO_DATABASE_ADMIN_URL"), _required("BOTCENTRO_PG_APP_PASSWORD"))
    print("plataforma lista")


def db_migrate(record_only: bool) -> None:
    from botcentro.db.migrate import migrate

    n = migrate(_required("BOTCENTRO_DATABASE_ADMIN_URL"), record_only=record_only)
    print(f"{n} migraciones {'registradas' if record_only else 'aplicadas'}")


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


def _socrata_connector(adapter: str, fetcher: SafeFetcher):  # noqa: ANN202
    from botcentro.investigations.connector import SocrataConnector
    from botcentro.investigations.datasets import SPECS

    return SocrataConnector(fetcher, SPECS[adapter])


def ingest(code: str, start: str, end: str, window_days: int, reparse: bool = False,
           filters: dict[str, Any] | None = None, max_pages: int = 1000) -> None:
    from botcentro.investigations.datasets import MAPPING_VERSION, SPECS
    from botcentro.investigations.socrata import SchemaIncompatible

    client = account_client("ingest")
    source = client.select("sources", {"code": f"eq.{code}", "select": "*"})
    if not source:
        sys.exit(f"fuente {code} inexistente o no visible para la cuenta de ingesta")
    source = source[0]
    if source["state"] not in ("active", "degraded"):
        sys.exit(f"{code} está en estado {source['state']}: no admite ingesta productiva")
    policy = client.select("source_policies", {"id": f"eq.{source['policy_id']}", "select": "*"})[0]
    socrata = source["adapter"] in SPECS
    if not socrata and CONNECTORS.get(source["adapter"]) is None:
        sys.exit(f"adaptador {source['adapter']} sin implementación")

    guard = UrlGuard(UrlPolicy.for_domains(source["allowed_domains"]))
    with SafeFetcher(guard) as fetcher:
        connector = _socrata_connector(source["adapter"], fetcher) if socrata else CONNECTORS[source["adapter"]](fetcher)
        if socrata:
            # Esquema antes de la primera página (SRC-04): un campo crítico ausente detiene la carga.
            try:
                report = connector.validate_source({})
            except SchemaIncompatible as exc:
                client.call("ingest_register_schema", {
                    "p_source_code": code, "p_fingerprint": exc.fingerprint, "p_fields": {}, "p_mapping_version": MAPPING_VERSION,
                    "p_result": "incompatible", "p_missing_critical": exc.missing_critical, "p_missing_optional": [],
                    "p_dataset_updated_at": None})
                sys.exit(f"{code}: esquema incompatible ({exc}); carga detenida")
            schema = connector.schema
            client.call("ingest_register_schema", {
                "p_source_code": code, "p_fingerprint": schema.fingerprint, "p_fields": schema.fields,
                "p_mapping_version": MAPPING_VERSION, "p_result": schema.result, "p_missing_critical": [],
                "p_missing_optional": schema.missing_optional,
                "p_dataset_updated_at": schema.dataset_updated_at.isoformat() if schema.dataset_updated_at else None})
            for line in report.diagnostics:
                print(f"degradado: {line}", file=sys.stderr)
        runner = IngestionRunner(
            source_id=source["id"],
            connector=connector,
            store=IngestStore(client),
            objects=LocalObjectStore(os.environ.get("BOTCENTRO_OBJECT_STORE_DIR", "var/objects")),
            profile=_profile(policy),
            reparse=reparse,
        )
        scope = {"from": start, "to": end, "window_days": window_days, **(filters or {})}
        previous = _resumable_cursor(client, source["id"], connector.version, scope)
        if previous:
            print(f"reanudando desde {previous['position']}", file=sys.stderr)
        summary = runner.run(mode="backfill", scope=scope, previous_cursor=previous, max_pages=max_pages)
    if socrata:
        client.call("ingest_source_status", {
            "p_source_code": code, "p_health": "healthy" if summary.status != "failed" else "unavailable",
            "p_coverage": "complete_for_scope" if summary.status == "succeeded" else "partial"})
    print(json.dumps({k: str(v) if k == "run_id" else v for k, v in summary.__dict__.items()}, ensure_ascii=False, indent=1))


# InsForge limita cada RPC a 10 s: los pasos por lotes se repiten hasta que no queda nada pendiente.
NORMALIZE_STEPS = ("catalog", "projects", "sessions", "votings", "vote_observations", "current_votes",
                   "attendance", "agenda")
BATCHED_STEPS = {"vote_observations", "current_votes", "attendance"}


CAMARA_STEPS = ("projects", "status", "authors")
INVESTIGATION_STEPS = {"SRC-20": "territories", "SRC-15": "contracts", "SRC-17": "contracts", "SRC-18": "siri",
                       "SRC-19": "documents", "SRC-25": "entity_plans"}


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
    elif code == "SRC-26":
        _run_steps(client, "normalize_session_videos", ("session_videos",), {"session_videos"}, 200)
        return
    elif code in INVESTIGATION_STEPS:
        _run_steps(client, "normalize_investigations", (INVESTIGATION_STEPS[code],), {INVESTIGATION_STEPS[code]}, 60)
        return
    else:
        sys.exit(f"{code} no tiene normalizador")
    client.call("maintenance_refresh_project_activity", {})  # lista de proyectos del bot (I5)
    print(json.dumps({"step": "project_activity", "refreshed": True}), flush=True)


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
    """Bot de Telegram (DEC-16, DEC-18): aiogram por sondeo largo, interfaz con menús y contexto en
    Redis, respuestas sin IA."""
    import asyncio
    import logging
    import socket

    from redis.asyncio import Redis

    from botcentro.config import _key
    from botcentro.embeddings.e5 import E5SmallEmbedder
    from botcentro.query.engine import AnswerEngine
    from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings
    from botcentro.telegram_ui.aiogram_adapter import AiogramTransport, make_bot, run_polling
    from botcentro.telegram_ui.app import UiApplication
    from botcentro.telegram_ui.digest import DigestWorker
    from botcentro.telegram_ui.investigations import InvestigationViews
    from botcentro.telegram_ui.runtime import UiRuntime
    from botcentro.telegram_ui.state import UiState

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx/aiohttp registran URLs y las de la Bot API contienen el token: nunca deben llegar al log.
    for noisy in ("httpx", "httpcore", "aiohttp.access", "aiogram.event"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    token = _required("BOTCENTRO_TELEGRAM_BOT_TOKEN")
    bot_id = int(token.split(":", 1)[0])
    pseudonym_key = _key(os.environ, "BOTCENTRO_PSEUDONYM_KEY")
    if pseudonym_key is None:
        sys.exit("falta BOTCENTRO_PSEUDONYM_KEY en .env")
    client = account_client("query")
    settings = WebhookSettings(bot_id, "sondeo-largo", pseudonym_key,
                               int(os.environ.get("BOTCENTRO_TELEGRAM_RATE_LIMIT", "10")))
    embedder = E5SmallEmbedder()
    engine = AnswerEngine(client, qdrant_store(), embedder.embed_query)

    async def main() -> None:
        redis = Redis.from_url(_required("BOTCENTRO_REDIS_URL"))
        bot = make_bot(token)
        transport = AiogramTransport(bot)
        state = UiState(redis, bot_id=bot_id)
        app = UiApplication(engine, investigations=InvestigationViews(client, bot_id=bot_id))
        runtime = UiRuntime(transport=transport, state=state,
                            app=app, intake=TelegramWebhook(settings, client), rpc=client,
                            bot_id=bot_id, pseudonym_key=pseudonym_key, worker_id=f"bot-{socket.gethostname()}",
                            message_limit=int(os.environ.get("BOTCENTRO_TELEGRAM_MESSAGE_LIMIT", "4096")))
        # Calentamiento antes de aceptar mensajes: el modelo de embeddings carga perezosamente (~2,5 s en la
        # primera pregunta) y la primera consulta abre conexiones a Qdrant, Redis y PostgreSQL.
        started = time.perf_counter()
        try:
            await asyncio.to_thread(engine.answer, "proyectos sobre salud")
            await redis.ping()
        except Exception as exc:  # noqa: BLE001 — el calentamiento nunca impide arrancar
            logging.getLogger("botcentro.ui").warning("calentamiento incompleto: %s", type(exc).__name__)
        me = await bot.get_me()
        print(json.dumps({"bot": me.username, "bot_id": bot_id, "adapter": "aiogram",
                          "calentamiento_ms": int((time.perf_counter() - started) * 1000)}), flush=True)
        try:
            await run_polling(bot, runtime, digest=DigestWorker(client, transport, redis, prefix=state.prefix))
        finally:
            await bot.session.close()
            await redis.aclose()

    asyncio.run(main())


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
    i.add_argument("--filters", default="{}", help='alcance adicional en JSON, p. ej. {"territories": [...]}')
    i.add_argument("--max-pages", type=int, default=1000)
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
    ms = sub.add_parser("muestra-secop", help="muestra de verificación de SECOP II contra datos.gov.co (en var/)")
    ms.add_argument("--por-municipio", type=int, default=7)
    ms.add_argument("--semilla", type=int, default=20260930)
    ia = sub.add_parser("index-actas", help="publica las actas de las gacetas con la sesión que registra cada una")
    ia.add_argument("--manifest", default="var/gacetas-manifest-local.jsonl")
    ia.add_argument("--backfill", type=int, default=0, help="recupera hasta N gacetas cargadas antes (sin PDF)")
    ia.add_argument("--pause", type=float, default=3.0)
    sub.add_parser("db-bootstrap", help="prepara roles, auth y extensiones en el PostgreSQL autoalojado")
    mg = sub.add_parser("migrate", help="aplica las migraciones pendientes en el PostgreSQL autoalojado")
    mg.add_argument("--record-only", action="store_true", help="solo registra (base restaurada con ese esquema)")
    args = parser.parse_args(argv)
    if args.command == "verify-account":
        verify_account(args.who, args.code)
    elif args.command == "muestra-secop":
        muestra_secop(args.por_municipio, args.semilla)
    elif args.command == "index-actas":
        index_actas(args.manifest, args.backfill, args.pause)
    elif args.command == "db-bootstrap":
        db_bootstrap()
    elif args.command == "migrate":
        db_migrate(args.record_only)
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
        ingest(args.source, args.start, args.end, args.window_days, args.reparse, json.loads(args.filters),
               args.max_pages)


if __name__ == "__main__":
    main()
