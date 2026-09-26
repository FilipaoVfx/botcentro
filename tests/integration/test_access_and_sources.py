"""Control de acceso (SRS-N01, T-24) y activación de fuentes (SRS-F01/F02, T-01)."""

from __future__ import annotations

import psycopg
import pytest

from botcentro.insforge.client import ActivationRejected, InvalidState, PermissionDenied
from tests.integration.factories import activate_source, new_source
from tests.integration.support import Db

pytestmark = pytest.mark.db


def test_anon_cannot_read_any_table(db: Db) -> None:
    tables = db.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind in ('r', 'v')"
    )
    assert len(tables) > 60
    for row in tables:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.as_user(None, f'select 1 from public."{row["relname"]}" limit 1')


def test_user_without_role_sees_nothing_and_cannot_call_rpc(db: Db) -> None:
    admin = db.create_user("admin")
    new_source(db)
    nobody = db.create_user()
    assert db.as_user(admin, "select count(*) as n from public.sources")[0]["n"] >= 1
    assert db.as_user(nobody, "select count(*) as n from public.sources")[0]["n"] == 0
    with pytest.raises(PermissionDenied):
        db.rpc(nobody).call("jobs_enqueue", {"p_kind": "ingest.fetch", "p_idempotency_key": "x"})


def test_audit_log_cannot_be_forged_or_read_by_services(db: Db) -> None:
    service = db.create_user("query_service")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.as_user(service, "select public.write_audit(null, 'x', 'y', 'z', null, null, 'falso')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.as_user(service, "insert into public.audit_log (action, target_type) values ('x', 'y')")
    assert db.as_user(service, "select count(*) as n from public.audit_log")[0]["n"] == 0


def test_append_only_tables_reject_direct_writes_even_for_ingest(db: Db) -> None:
    ingest = db.create_user("ingest_service")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.as_user(ingest, "update public.source_snapshots set final_url = 'x'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db.as_user(ingest, "delete from public.observations")


def test_api_user_sees_only_own_queries(db: Db) -> None:
    alice, bob = db.create_user(), db.create_user()
    for user in (alice, bob):
        principal = db.execute(
            "insert into public.principals (kind, auth_user_id) values ('auth_user', %s) returning id", (user,)
        )[0]["id"]
        db.execute(
            "insert into public.query_runs (principal_id, channel, question_text) values (%s, 'api', %s)",
            (principal, f"pregunta de {user}"),
        )
    rows = db.as_user(alice, "select question_text from public.query_runs")
    assert rows == [{"question_text": f"pregunta de {alice}"}]


def test_activation_rejected_with_concrete_diagnostics(db: Db) -> None:
    admin = db.create_user("admin")
    source_id = new_source(db)
    with pytest.raises(ActivationRejected) as info:
        db.rpc(admin).call("admin_set_source_state", {"p_source_id": source_id, "p_state": "active",
                                                      "p_reason": "intento prematuro"})
    assert "sin perfil de uso" in (info.value.detail or "")
    assert "sin alcance de cobertura" in (info.value.detail or "")


def test_activation_succeeds_with_reviewed_policy_and_coverage(db: Db) -> None:
    admin = db.create_user("admin")
    source_id = new_source(db)
    activate_source(db, source_id, admin)
    row = db.execute("select state, policy_id from public.sources where id = %s", (source_id,))[0]
    assert row["state"] == "active" and row["policy_id"] is not None
    audit = db.execute("select action from public.audit_log where target_id = %s order by id", (str(source_id),))
    assert [a["action"] for a in audit] == ["source.add_policy", "source.set_state"]


def test_operator_can_suspend_but_not_activate(db: Db) -> None:
    admin, operator = db.create_user("admin"), db.create_user("operator")
    source_id = new_source(db)
    activate_source(db, source_id, admin)
    rpc = db.rpc(operator)
    rpc.call("admin_set_source_state", {"p_source_id": source_id, "p_state": "suspended", "p_reason": "caída de fuente"})
    with pytest.raises(PermissionDenied):
        rpc.call("admin_set_source_state", {"p_source_id": source_id, "p_state": "active", "p_reason": "reanudar"})


def test_policies_are_versioned_and_immutable(db: Db) -> None:
    admin = db.create_user("admin")
    source_id = new_source(db)
    activate_source(db, source_id, admin)
    with pytest.raises(psycopg.Error) as info:
        db.execute("update public.source_policies set capture_metadata = 'denied' where source_id = %s", (source_id,))
    assert info.value.sqlstate == "BC001"


def test_productive_run_requires_active_source(db: Db) -> None:
    ingest = db.create_user("ingest_service")
    source_id = new_source(db)
    with pytest.raises(InvalidState):
        db.rpc(ingest).call("ingest_start_run", {"p_source_id": source_id, "p_mode": "incremental",
                                                  "p_connector_version": "1.0.0"})
    run_id = db.rpc(ingest).call("ingest_start_run", {"p_source_id": source_id, "p_mode": "validation",
                                                       "p_connector_version": "1.0.0"})
    assert run_id


def test_published_observation_requires_evidence(db: Db) -> None:
    admin = db.create_user("admin")
    source_id = new_source(db)
    activate_source(db, source_id, admin)
    record = db.execute(
        "insert into public.source_records (source_id, record_type, logical_key) values (%s, 'project', 'k') returning id",
        (source_id,),
    )[0]["id"]
    snapshot = db.execute(
        """insert into public.source_snapshots (record_id, requested_url, final_url, content_hash, byte_size,
                                               adapter_version, fetched_at)
           values (%s, 'https://x', 'https://x', repeat('a', 64), 1, '1', now()) returning id""",
        (record,),
    )[0]["id"]
    with pytest.raises(psycopg.Error) as info:
        db.execute(
            """insert into public.observations (observation_key, source_id, first_snapshot_id, authority, subject_type,
                   subject_ref, predicate, value_json, first_observed_at, last_observed_at, status, parser_version)
               values (repeat('b', 64), %s, %s, 'primary', 'project', 'x', 'title', '{}', now(), now(), 'published', '1')""",
            (source_id, snapshot),
        )
    assert info.value.sqlstate == "BC003"
