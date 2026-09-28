"""Orquestación de una ejecución de ingesta (SRS-F03..F06, §7; T-02, T-04, T-12).

Flujo por página: descubrir → por ítem: descargar → guardar original (si la política lo
permite) → registrar captura → si el contenido cambió, normalizar → registrar observaciones
con evidencia → encolar documentos enlazados. El cursor se confirma solo después de
persistir la página completa: un fallo a mitad reprocesa la página, y la idempotencia de
las RPC evita duplicados.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from botcentro.connectors.base import Connector, Cursor, DiscoveredItem
from botcentro.connectors.retry import Action, decide
from botcentro.domain.hashing import idempotency_key
from botcentro.errors import FailureKind, FetchError, PolicyViolation
from botcentro.http.fetcher import Fetched, NotModified
from botcentro.ingest.store import IngestStore
from botcentro.sources.policy import Operation, UsageProfile
from botcentro.storage.objects import ObjectStore

log = logging.getLogger(__name__)


class SourceSuspended(Exception):
    """La fuente respondió con un fallo de autenticación/permisos y quedó suspendida."""


@dataclass
class RunSummary:
    run_id: UUID
    status: str = "succeeded"
    pages: int = 0
    discovered: int = 0
    new_snapshots: int = 0
    unchanged: int = 0
    observations_created: int = 0
    observations_seen: int = 0
    quarantined: int = 0
    failed: int = 0
    documents_enqueued: int = 0
    errors: list[str] = field(default_factory=list)


class IngestionRunner:
    def __init__(self, *, source_id: UUID, connector: Connector, store: IngestStore, objects: ObjectStore,
                 profile: UsageProfile, reparse: bool = False) -> None:
        self.source_id = source_id
        self.connector = connector
        self.store = store
        self.objects = objects
        self.profile = profile
        # Reinterpretar capturas sin cambios (nueva versión del parser). Idempotente.
        self.reparse = reparse

    def run(self, *, mode: str, scope: Mapping[str, Any], previous_cursor: Mapping[str, Any] | None = None,
            coverage_scope_id: UUID | None = None, max_pages: int = 1000) -> RunSummary:
        self.profile.require(Operation.CAPTURE_METADATA)
        cursor = Cursor.from_json(previous_cursor, connector_version=self.connector.version, scope=scope)
        run_id = self.store.start_run(self.source_id, mode, self.connector.version,
                                      coverage_scope_id=coverage_scope_id, cursor_before=cursor.to_json())
        summary = RunSummary(run_id)
        try:
            for _ in range(max_pages):
                page = self.connector.discover(cursor)
                summary.pages += 1
                summary.discovered += len(page.items)
                self.store.add_counters(run_id, discovered=len(page.items))
                for item in page.items:
                    self._process_item(run_id, item, summary)
                self.store.commit_cursor(run_id, page.next_cursor)
                cursor = page.next_cursor
                if not page.has_more:
                    break
        except SourceSuspended as exc:
            summary.status = "failed"
            summary.errors.append(str(exc))
            self.store.finish_run(run_id, "failed", "SOURCE_SUSPENDED")
            return summary
        except Exception:
            self.store.finish_run(run_id, "failed", "UNEXPECTED_ERROR")
            raise

        if summary.failed or summary.quarantined:
            summary.status = "partial"
        self.store.finish_run(run_id, summary.status)
        return summary

    def _process_item(self, run_id: UUID, item: DiscoveredItem, summary: RunSummary) -> None:
        if item.is_file and not self.profile.permits(Operation.DOWNLOAD_FILES):
            summary.errors.append(f"{item.logical_key}: descarga no permitida por el perfil de uso")
            return
        try:
            result = self.connector.fetch(item)
        except FetchError as exc:
            self._handle_fetch_error(run_id, item, exc, summary)
            return

        if isinstance(result, NotModified):
            self.store.register_check(run_id, item.logical_key, "not_modified", http_status=result.status)
            summary.unchanged += 1
            return
        self._register_snapshot(run_id, item, result, summary)

    def _register_snapshot(self, run_id: UUID, item: DiscoveredItem, fetched: Fetched, summary: RunSummary) -> None:
        object_key = None
        if self.profile.permits(Operation.RETAIN_CONTENT):
            object_key = self.objects.put(fetched.content, fetched.content_hash, fetched.sniffed_mime)

        registration = self.store.register_fetch(run_id, item, fetched, object_key=object_key,
                                                 adapter_version=self.connector.version)
        if not registration.content_changed:
            summary.unchanged += 1
            if not self.reparse:
                return
        else:
            summary.new_snapshots += 1

        parsed = self.connector.parse(item, fetched)
        schema_problem = None
        if parsed.schema_changed:
            schema_problem = "; ".join(i.message for i in parsed.issues)
        elif parsed.is_empty_mapping and not item.is_file:
            schema_problem = "el parser no produjo datos para un registro con contenido"
        if schema_problem:
            self.store.open_review_case(
                "schema",
                f"schema:{self.source_id}:{item.record_type}:{self.connector.parser_version}",
                f"Posible cambio de esquema en {item.record_type}: {schema_problem}",
                run_id=run_id,
            )

        reason = f"cambio de esquema: {schema_problem}" if schema_problem else None
        created, seen = self.store.upsert_observations(
            registration.snapshot_id, parsed.candidates, parser_version=self.connector.parser_version,
            force_quarantine_reason=reason,
        )
        summary.observations_created += created
        summary.observations_seen += seen
        quarantined = sum(bool(c.quarantine_reason or schema_problem) for c in parsed.candidates)
        # Un registro sin candidatos por cambio de esquema cuenta como un ítem en cuarentena.
        quarantined_items = quarantined if parsed.candidates else int(schema_problem is not None)
        summary.quarantined += quarantined_items
        self.store.add_counters(run_id, normalized=len(parsed.candidates) - quarantined,
                                quarantined=quarantined_items)

        if parsed.document_links and self.profile.permits(Operation.DOWNLOAD_FILES):
            for link in parsed.document_links:
                self.store.enqueue(
                    "ingest.document_fetch",
                    idempotency_key(str(self.source_id), link.logical_key),
                    {"source_id": str(self.source_id), "url": link.url, "logical_key": link.logical_key,
                     "document_type": link.document_type, "title": link.title,
                     "parent_snapshot_id": str(registration.snapshot_id)},
                    source_id=self.source_id, run_id=run_id,
                )
                summary.documents_enqueued += 1

    def _handle_fetch_error(self, run_id: UUID, item: DiscoveredItem, exc: FetchError, summary: RunSummary) -> None:
        decision = decide(exc.kind, attempt=1, retry_after=exc.retry_after)
        if decision.action is Action.SUSPEND_SOURCE:
            self.store.register_check(run_id, item.logical_key, "error", http_status=exc.status, error_code=exc.code)
            reason = f"{exc.code} en {item.url}: {exc.message}"
            self.store.suspend_source(self.source_id, reason)
            raise SourceSuspended(reason)
        if decision.action is Action.SKIP:
            self.store.register_check(run_id, item.logical_key, "not_found", http_status=exc.status, error_code=exc.code)
            return
        # Transitorios: el reintento es del trabajo completo (la página se reprocesa de forma
        # idempotente). Política/contenido/límites: caso de revisión.
        summary.failed += 1
        summary.errors.append(f"{item.logical_key}: {exc.code}")
        # ingest_register_check ya suma el fallo al contador de la ejecución.
        self.store.register_check(run_id, item.logical_key, "error", http_status=exc.status, error_code=exc.code)
        if exc.kind in (FailureKind.POLICY, FailureKind.RESOURCE_LIMIT, FailureKind.INVALID_CONTENT):
            self.store.open_review_case(
                "quality", f"fetch:{self.source_id}:{item.logical_key}:{exc.code}",
                f"No se pudo adquirir {item.url}: {exc.message}", run_id=run_id,
            )


__all__ = ["IngestionRunner", "PolicyViolation", "RunSummary", "SourceSuspended"]
