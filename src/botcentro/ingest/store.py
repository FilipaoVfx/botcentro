"""Persistencia de la ingesta mediante las RPC `ingest_*` (SRS-F03..F06, F10)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from botcentro.connectors.base import Cursor, DiscoveredItem, NormalizedCandidate
from botcentro.domain.enums import DatePrecision
from botcentro.http.fetcher import Fetched
from botcentro.insforge.client import RpcClient, as_rows


@dataclass(frozen=True)
class FetchRegistration:
    record_id: UUID
    snapshot_id: UUID
    result: str  # new_snapshot | reverted | unchanged

    @property
    def content_changed(self) -> bool:
        return self.result != "unchanged"


@dataclass(frozen=True)
class ObservationResult:
    observation_id: UUID
    created: bool
    evidence_id: UUID


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class IngestStore:
    def __init__(self, rpc: RpcClient) -> None:
        self._rpc = rpc

    def start_run(self, source_id: UUID, mode: str, connector_version: str, *,
                  coverage_scope_id: UUID | None = None, cursor_before: Mapping[str, Any] | None = None) -> UUID:
        return _uuid(self._rpc.call("ingest_start_run", {
            "p_source_id": source_id,
            "p_mode": mode,
            "p_connector_version": connector_version,
            "p_coverage_scope_id": coverage_scope_id,
            "p_cursor_before": dict(cursor_before) if cursor_before is not None else None,
        }))

    def register_fetch(self, run_id: UUID, item: DiscoveredItem, fetched: Fetched, *, object_key: str | None,
                       adapter_version: str) -> FetchRegistration:
        row = as_rows(self._rpc.call("ingest_register_fetch", {
            "p_run_id": run_id,
            "p_record_type": item.record_type,
            "p_logical_key": item.logical_key,
            "p_external_id": item.external_id,
            "p_canonical_url": item.url,
            "p_requested_url": fetched.requested_url,
            "p_final_url": fetched.final_url,
            "p_http_status": fetched.status,
            "p_content_hash": fetched.content_hash,
            "p_byte_size": fetched.byte_size,
            "p_mime_type": fetched.sniffed_mime,
            "p_object_key": object_key,
            "p_etag": fetched.etag,
            "p_last_modified": fetched.last_modified,
            "p_adapter_version": adapter_version,
            "p_fetched_at": fetched.fetched_at,
        }))[0]
        return FetchRegistration(_uuid(row["record_id"]), _uuid(row["snapshot_id"]), row["result"])

    def register_check(self, run_id: UUID, logical_key: str, result: str, *, http_status: int | None = None,
                       error_code: str | None = None, detail: str | None = None) -> UUID | None:
        value = self._rpc.call("ingest_register_check", {
            "p_run_id": run_id,
            "p_logical_key": logical_key,
            "p_result": result,
            "p_http_status": http_status,
            "p_error_code": error_code,
            "p_detail": detail,
        })
        return _uuid(value) if value else None

    def upsert_observation(self, snapshot_id: UUID, candidate: NormalizedCandidate, *, parser_version: str,
                           force_quarantine_reason: str | None = None) -> ObservationResult:
        reason = force_quarantine_reason or candidate.quarantine_reason
        precision = candidate.effective.precision
        row = as_rows(self._rpc.call("ingest_upsert_observation", {
            "p_snapshot_id": snapshot_id,
            "p_subject_type": candidate.subject_type,
            "p_subject_ref": candidate.subject_ref,
            "p_predicate": candidate.predicate,
            "p_value_json": dict(candidate.value),
            "p_parser_version": parser_version,
            "p_value_raw": candidate.value_raw,
            "p_effective_date": candidate.effective.value,
            "p_date_precision": precision.value if isinstance(precision, DatePrecision) else precision,
            "p_effective_at": candidate.effective_at,
            "p_published_on": candidate.published_on.value,
            "p_record_pointer": candidate.record_pointer,
            "p_status": "quarantined" if reason else "published",
            "p_status_reason": reason,
        }))[0]
        return ObservationResult(_uuid(row["observation_id"]), bool(row["created"]), _uuid(row["evidence_id"]))

    def commit_cursor(self, run_id: UUID, cursor: Cursor) -> None:
        self._rpc.call("ingest_commit_cursor", {"p_run_id": run_id, "p_cursor": cursor.to_json()})

    def add_counters(self, run_id: UUID, *, discovered: int = 0, normalized: int = 0, quarantined: int = 0,
                     failed: int = 0) -> None:
        if discovered or normalized or quarantined or failed:
            self._rpc.call("ingest_add_counters", {
                "p_run_id": run_id, "p_discovered": discovered, "p_normalized": normalized,
                "p_quarantined": quarantined, "p_failed": failed,
            })

    def finish_run(self, run_id: UUID, status: str, error_code: str | None = None) -> None:
        self._rpc.call("ingest_finish_run", {"p_run_id": run_id, "p_status": status, "p_error_code": error_code})

    def suspend_source(self, source_id: UUID, reason: str, *, state: str = "suspended") -> None:
        self._rpc.call("ingest_suspend_source", {"p_source_id": source_id, "p_state": state, "p_reason": reason})

    def open_review_case(self, case_type: str, dedupe_key: str, summary: str, *, subject_type: str | None = None,
                         subject_id: UUID | None = None, candidates: Sequence[Mapping[str, Any]] = (),
                         evidence_ids: Sequence[UUID] = (), run_id: UUID | None = None) -> tuple[UUID, bool]:
        row = as_rows(self._rpc.call("review_open_case", {
            "p_case_type": case_type,
            "p_dedupe_key": dedupe_key,
            "p_summary": summary,
            "p_subject_type": subject_type,
            "p_subject_id": subject_id,
            "p_candidates": [dict(c) for c in candidates],
            "p_evidence_ids": list(evidence_ids),
            "p_run_id": run_id,
        }))[0]
        return _uuid(row["case_id"]), bool(row["created"])

    def enqueue(self, kind: str, idempotency_key: str, payload: Mapping[str, Any], *,
                source_id: UUID | None = None, run_id: UUID | None = None) -> tuple[UUID, bool]:
        row = as_rows(self._rpc.call("jobs_enqueue", {
            "p_kind": kind,
            "p_idempotency_key": idempotency_key,
            "p_payload": dict(payload),
            "p_source_id": source_id,
            "p_run_id": run_id,
        }))[0]
        return _uuid(row["job_id"]), bool(row["created"])
