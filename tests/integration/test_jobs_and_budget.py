"""Cola durable (T-03) y reserva atómica de presupuesto (T-29)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from botcentro.costs.budget import BudgetExceeded, BudgetGuard
from botcentro.errors import FailureKind, FetchError
from botcentro.insforge.client import PermissionDenied
from botcentro.jobs.queue import JobQueue, Worker
from tests.integration.factories import unique
from tests.integration.support import Db

pytestmark = pytest.mark.db


def test_enqueue_is_idempotent_and_claim_leases(db: Db) -> None:
    queue = JobQueue(db.rpc(db.create_user("ingest_service")))
    key = unique("k")
    first, created = queue.enqueue("ingest.fetch", key, {"n": 1})
    again, created_again = queue.enqueue("ingest.fetch", key, {"n": 2})
    assert first == again and created and not created_again

    claimed = [j for j in queue.claim("w1", ["ingest.fetch"], limit=10) if j.id == first]
    assert claimed and claimed[0].attempts == 1 and claimed[0].payload == {"n": 1}
    assert all(j.id != first for j in queue.claim("w2", ["ingest.fetch"], limit=10))


def test_services_only_claim_their_own_job_kinds(db: Db) -> None:
    queue = JobQueue(db.rpc(db.create_user("query_service")))
    with pytest.raises(PermissionDenied):
        queue.claim("w", ["ingest.fetch"])
    with pytest.raises(PermissionDenied):
        queue.enqueue("ingest.fetch", unique("k"))


def test_fencing_and_recovery_after_worker_crash(db: Db) -> None:
    queue = JobQueue(db.rpc(db.create_user("ingest_service")))
    job_id, _ = queue.enqueue("ingest.crash", unique("k"))
    assert [j.id for j in queue.claim("dead-worker", ["ingest.crash"])] == [job_id]

    # El worker muere: su lease vence.
    db.execute("update public.jobs set lease_until = now() - interval '1 second' where id = %s", (job_id,))
    assert queue.release_expired() >= 1
    state = db.execute("select state, last_error_code from public.jobs where id = %s", (job_id,))[0]
    assert state == {"state": "retry_wait", "last_error_code": "LEASE_EXPIRED"}

    reclaimed = queue.claim("new-worker", ["ingest.crash"])
    assert [j.id for j in reclaimed] == [job_id] and reclaimed[0].attempts == 2
    assert queue.complete(job_id, "dead-worker") is False  # fencing: el lease ya no es suyo
    assert queue.complete(job_id, "new-worker") is True
    assert db.execute("select state from public.jobs where id = %s", (job_id,))[0]["state"] == "succeeded"


def test_worker_retries_transient_and_dead_letters_permanent_failures(db: Db) -> None:
    queue = JobQueue(db.rpc(db.create_user("ingest_service")))
    transient_id, _ = queue.enqueue("ingest.transient", unique("k"))
    permanent_id, _ = queue.enqueue("ingest.permanent", unique("k"))
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    def transient(job):
        raise FetchError("HTTP_503", "no disponible", kind=FailureKind.TRANSIENT)

    def permanent(job):
        raise FetchError("RESOURCE_LIMIT", "demasiado grande", kind=FailureKind.RESOURCE_LIMIT)

    worker = Worker(queue, {"ingest.transient": transient, "ingest.permanent": permanent}, worker_id="w",
                    clock=lambda: now)
    assert worker.run_once(limit=10) >= 2
    rows = {r["id"]: r for r in db.execute(
        "select id, state, run_at, last_error_code from public.jobs where id in (%s, %s)", (transient_id, permanent_id))}
    assert rows[transient_id]["state"] == "retry_wait"
    assert rows[transient_id]["run_at"] >= now + timedelta(seconds=15)
    assert rows[permanent_id]["state"] == "dead_letter" and rows[permanent_id]["last_error_code"] == "RESOURCE_LIMIT"


def test_budget_requires_configuration(db: Db) -> None:
    guard = BudgetGuard(db.rpc(db.create_user("query_service")))
    with pytest.raises(BudgetExceeded, match="sin presupuesto"):
        guard.reserve(unique("proveedor"), "embeddings", "0.01")


def test_concurrent_reservations_never_exceed_limit(db: Db) -> None:
    provider = unique("ocr")
    db.execute("insert into public.budgets (name, provider, period, limit_amount) values (%s, %s, 'daily', 1.00)",
               (unique("presupuesto"), provider))
    guard = BudgetGuard(db.rpc(db.create_user("ingest_service")))

    def attempt(_: int) -> bool:
        try:
            guard.reserve(provider, "ocr_page", "0.10")
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(25)))
    assert sum(results) == 10
    total = db.execute("select sum(estimated_cost) as total from public.usage_ledger where provider = %s", (provider,))
    assert total[0]["total"] == Decimal("1.000000")


def test_spend_settles_actual_cost_and_releases_on_error(db: Db) -> None:
    provider = unique("llm")
    db.execute("insert into public.budgets (name, provider, period, limit_amount) values (%s, %s, 'monthly', 10)",
               (unique("presupuesto"), provider))
    guard = BudgetGuard(db.rpc(db.create_user("query_service")))
    with guard.spend(provider, "generation", "1.00") as reservation:
        reservation.settle("0.40")
    with pytest.raises(RuntimeError):
        with guard.spend(provider, "generation", "2.00"):
            raise RuntimeError("fallo del proveedor")
    rows = db.execute("select state, settled_cost from public.usage_ledger where provider = %s order by reserved_at",
                      (provider,))
    assert rows == [{"state": "settled", "settled_cost": Decimal("0.400000")},
                    {"state": "released", "settled_cost": None}]


def test_alert_threshold_is_reported(db: Db) -> None:
    provider = unique("emb")
    db.execute("insert into public.budgets (name, provider, period, limit_amount) values (%s, %s, 'daily', 1)",
               (unique("presupuesto"), provider))
    guard = BudgetGuard(db.rpc(db.create_user("ingest_service")))
    assert guard.reserve(provider, "x", "0.5").alert is False
    assert guard.reserve(provider, "x", "0.35").alert is True
