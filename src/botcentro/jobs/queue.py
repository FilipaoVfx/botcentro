"""Cola durable sobre las RPC `jobs_*` y bucle de worker (SRS §3.2, §7.3; T-03).

Entrega al menos una vez: un handler debe ser idempotente respecto a su trabajo. El lease
tiene expiración y heartbeat; si el worker muere, jobs_release_expired devuelve el trabajo a
la cola. Completar o fallar exige seguir siendo dueño del lease (fencing).
"""

from __future__ import annotations

import logging
import random
import socket
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from botcentro.connectors.retry import Action, RetryPolicy, decide
from botcentro.errors import BotcentroError, FailureKind
from botcentro.insforge.client import RpcClient, as_rows

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Job:
    id: UUID
    kind: str
    payload: Mapping[str, Any]
    attempts: int
    max_attempts: int
    trace_id: UUID
    source_id: UUID | None = None
    run_id: UUID | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Job:
        def opt(value: Any) -> UUID | None:
            return UUID(str(value)) if value else None

        return cls(
            id=UUID(str(row["id"])),
            kind=row["kind"],
            payload=row.get("payload") or {},
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            trace_id=UUID(str(row["trace_id"])),
            source_id=opt(row.get("source_id")),
            run_id=opt(row.get("run_id")),
        )


class JobQueue:
    def __init__(self, rpc: RpcClient) -> None:
        self._rpc = rpc

    def enqueue(self, kind: str, idempotency_key: str, payload: Mapping[str, Any] | None = None, *,
                run_at: datetime | None = None, priority: int = 100, max_attempts: int = 5,
                processing_version: str = "1") -> tuple[UUID, bool]:
        row = as_rows(self._rpc.call("jobs_enqueue", {
            "p_kind": kind,
            "p_idempotency_key": idempotency_key,
            "p_payload": dict(payload or {}),
            "p_processing_version": processing_version,
            "p_run_at": run_at,
            "p_priority": priority,
            "p_max_attempts": max_attempts,
        }))[0]
        return UUID(str(row["job_id"])), bool(row["created"])

    def claim(self, worker_id: str, kinds: Sequence[str], *, limit: int = 1, lease_seconds: int = 300) -> list[Job]:
        rows = as_rows(self._rpc.call("jobs_claim", {
            "p_worker": worker_id, "p_kinds": list(kinds), "p_limit": limit, "p_lease_seconds": lease_seconds,
        }))
        return [Job.from_row(r) for r in rows]

    def heartbeat(self, job_id: UUID, worker_id: str, *, lease_seconds: int = 300) -> bool:
        return bool(self._rpc.call("jobs_heartbeat", {"p_job_id": job_id, "p_worker": worker_id,
                                                      "p_lease_seconds": lease_seconds}))

    def complete(self, job_id: UUID, worker_id: str) -> bool:
        return bool(self._rpc.call("jobs_complete", {"p_job_id": job_id, "p_worker": worker_id}))

    def fail(self, job_id: UUID, worker_id: str, error_code: str, error: str | None = None, *,
             retry_at: datetime | None = None) -> str | None:
        return self._rpc.call("jobs_fail", {
            "p_job_id": job_id, "p_worker": worker_id, "p_error_code": error_code,
            "p_error": error, "p_retry_at": retry_at,
        })

    def release_expired(self) -> int:
        return int(self._rpc.call("jobs_release_expired", {}) or 0)


Handler = Callable[[Job], None]


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


class Worker:
    """Procesa trabajos de los tipos registrados; los errores se clasifican con decide()."""

    def __init__(self, queue: JobQueue, handlers: Mapping[str, Handler], *, worker_id: str | None = None,
                 retry_policy: RetryPolicy = RetryPolicy(), lease_seconds: int = 300,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 rng: random.Random | None = None) -> None:
        if not handlers:
            raise ValueError("el worker necesita al menos un handler")
        self.queue = queue
        self.handlers = dict(handlers)
        self.worker_id = worker_id or default_worker_id()
        self.retry_policy = retry_policy
        self.lease_seconds = lease_seconds
        self._clock = clock
        self._rng = rng or random.Random()

    def run_once(self, *, limit: int = 1) -> int:
        """Toma y procesa hasta `limit` trabajos; devuelve cuántos procesó."""
        jobs = self.queue.claim(self.worker_id, list(self.handlers), limit=limit, lease_seconds=self.lease_seconds)
        for job in jobs:
            self._process(job)
        return len(jobs)

    def _process(self, job: Job) -> None:
        try:
            self.handlers[job.kind](job)
        except Exception as exc:  # noqa: BLE001 — todo fallo se clasifica y registra
            self._fail(job, exc)
            return
        if not self.queue.complete(job.id, self.worker_id):
            log.warning("lease perdido al completar %s (%s)", job.id, job.kind)

    def _fail(self, job: Job, exc: Exception) -> None:
        if isinstance(exc, BotcentroError):
            kind, code, retry_after = exc.kind, exc.code, exc.retry_after
        else:
            kind, code, retry_after = FailureKind.UNKNOWN, type(exc).__name__, None
        policy = RetryPolicy(job.max_attempts, self.retry_policy.base_delay, self.retry_policy.max_delay)
        decision = decide(kind, job.attempts, policy, retry_after=retry_after, rng=self._rng)
        retry_at = None
        if decision.action is Action.RETRY and decision.delay_seconds is not None:
            retry_at = self._clock() + timedelta(seconds=decision.delay_seconds)
        state = self.queue.fail(job.id, self.worker_id, code, str(exc)[:2000], retry_at=retry_at)
        log.warning("trabajo %s (%s) falló con %s → %s", job.id, job.kind, code, state)
