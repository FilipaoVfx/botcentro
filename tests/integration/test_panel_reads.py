"""Lecturas del panel (PW-01..PW-13) y rol inicial por email verificado."""

from __future__ import annotations

import json

import pytest

from tests.integration.factories import active_source, new_source
from tests.integration.support import Db

pytestmark = pytest.mark.db

READS = ["ops_overview", "ops_sources", "ops_runs", "ops_jobs", "ops_documents", "ops_queries",
         "ops_review_cases", "ops_costs", "ops_audit"]


def test_users_without_role_get_explicit_denial_not_zeros(db: Db) -> None:
    rpc = db.rpc(db.create_user())
    for fn in READS:
        result = rpc.call(fn, {})
        assert result["access"] is False, fn
        assert "items" not in result and "stages" not in result
    caps = rpc.call("ops_capabilities", {})
    assert caps["roles"] == [] and caps["views"]["ops"] is False


def test_admin_sees_every_section(db: Db) -> None:
    admin = db.create_user("admin")
    active_source(db, admin)
    new_source(db)
    rpc = db.rpc(admin)
    for fn in READS:
        assert rpc.call(fn, {})["access"] is True, fn
    overview = rpc.call("ops_overview", {})
    stages = {s["stage"]: s for s in overview["stages"]}
    assert list(stages) == ["acquisition", "normalization", "documents", "index", "queries", "delivery", "review"]
    assert all(s["access"] for s in stages.values())
    kinds = {a["kind"] for a in overview["attention"]}
    assert {"no_budget", "sources_pending_discovery"} <= kinds
    assert overview["sources"]["by_state"]["active"] >= 1
    caps = rpc.call("ops_capabilities", {})
    assert caps["is_admin"] and caps["actions_enabled"] is False
    assert {c["capability"] for c in caps["not_instrumented"]} >= {"workers", "alerts", "incidents", "logs"}


def test_reviewer_is_denied_queries_and_audit(db: Db) -> None:
    rpc = db.rpc(db.create_user("reviewer"))
    assert rpc.call("ops_review_cases", {})["access"] is True
    assert rpc.call("ops_queries", {})["access"] is False
    assert rpc.call("ops_audit", {})["access"] is False
    stages = {s["stage"]: s for s in rpc.call("ops_overview", {})["stages"]}
    assert stages["queries"] == {"stage": "queries", "label": "Consultas", "access": False}


def test_source_detail(db: Db) -> None:
    admin = db.create_user("admin")
    source_id = active_source(db, admin)
    detail = db.rpc(admin).call("ops_source", {"p_id": source_id})
    assert detail["source"]["state"] == "active" and detail["policy"]["version"] == 1
    assert detail["runs"] == [] and len(detail["coverage"]) == 1


def test_pending_role_is_claimed_only_with_verified_email(db: Db) -> None:
    db.execute("insert into public.pending_role_grants (email, role, reason) values "
               "('dueno@example.org', 'admin', 'responsable del proyecto')")
    unverified = db.create_user(email="dueno@example.org")
    assert db.rpc(unverified).call("claim_pending_roles", {}) == []
    db.execute("update auth.users set email_verified = true where id = %s", (unverified,))
    assert db.rpc(unverified).call("claim_pending_roles", {}) == ["admin"]
    assert db.rpc(unverified).call("claim_pending_roles", {}) == []  # una sola vez
    assert db.rpc(unverified).call("ops_capabilities", {})["is_admin"] is True
    audit = db.execute("select action from public.audit_log where target_id = %s", (str(unverified),))
    assert audit == [{"action": "role.claim_pending"}]


def test_queries_view_never_exposes_question_text(db: Db) -> None:
    admin = db.create_user("admin")
    principal = db.execute("insert into public.principals (kind) values ('telegram_user') returning id")[0]["id"]
    db.execute("insert into public.query_runs (principal_id, channel, question_text) values (%s, 'telegram', %s)",
               (principal, "texto privado del usuario"))
    result = db.rpc(admin).call("ops_queries", {})
    assert "texto privado" not in json.dumps(result)
    assert result["items"][0]["principal"] == str(principal)[:8]
