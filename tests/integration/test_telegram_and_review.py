"""Recepción de Telegram (T-21), revisión auditada (T-23), retención (SRS-N05) y búsqueda."""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from botcentro.insforge.client import InvalidArgument, InvalidState
from botcentro.telegram.security import pseudonymize_user
from botcentro.telegram.webhook import TelegramWebhook, WebhookSettings
from tests.integration.factories import active_source, unique
from tests.integration.support import Db

pytestmark = pytest.mark.db

BOT_ID = 777
KEY = b"k" * 32
SECRET = "s" * 24


def _update(update_id: int, user_id: int, text: str | None = "¿Qué pasa con el PL 249?", chat_type: str = "private") -> bytes:
    message = {"message_id": 1, "date": 0, "chat": {"id": user_id, "type": chat_type},
               "from": {"id": user_id, "is_bot": False}}
    if text is not None:
        message["text"] = text
    return json.dumps({"update_id": update_id, "message": message}).encode()


@pytest.fixture()
def webhook(db: Db) -> TelegramWebhook:
    return TelegramWebhook(WebhookSettings(BOT_ID, SECRET, KEY, rate_limit_per_minute=3),
                           db.rpc(db.create_user("query_service")))


def _authorize(db: Db, user_id: int) -> UUID:
    admin = db.create_user("admin")
    return db.rpc(admin).call("admin_authorize_telegram", {
        "p_bot_id": BOT_ID, "p_user_hash": pseudonymize_user(KEY, BOT_ID, user_id), "p_chat_id": user_id,
        "p_label": "piloto", "p_reason": "participante del piloto"})


def test_duplicate_update_creates_one_query_and_one_job(db: Db, webhook: TelegramWebhook) -> None:
    _authorize(db, 1001)
    first = webhook.handle(SECRET, _update(5001, 1001))
    second = webhook.handle(SECRET, _update(5001, 1001))
    assert (first.status, first.duplicate, second.status, second.duplicate) == ("accepted", False, "accepted", True)
    assert first.reply is None and second.reply is None
    jobs = db.execute("select kind, payload from public.jobs where idempotency_key = %s", (f"tg:{BOT_ID}:5001",))
    assert len(jobs) == 1 and jobs[0]["kind"] == "query.telegram_message"
    runs = db.execute("select question_text, status from public.query_runs where id = %s",
                      (jobs[0]["payload"]["query_run_id"],))
    assert runs == [{"question_text": "¿Qué pasa con el PL 249?", "status": "queued"}]


def test_unauthorized_user_is_not_stored_and_gets_informative_reply(db: Db, webhook: TelegramWebhook) -> None:
    result = webhook.handle(SECRET, _update(5002, 2002))
    assert result.status == "rejected_unauthorized"
    assert result.reply and result.reply["chat_id"] == 2002 and "2002" in result.reply["text"]
    stored = db.execute("select telegram_identity_id, query_run_id from public.telegram_updates "
                        "where bot_id = %s and update_id = 5002", (BOT_ID,))
    assert stored == [{"telegram_identity_id": None, "query_run_id": None}]
    assert db.execute("select count(*) as n from public.telegram_identities where chat_id = 2002")[0]["n"] == 0


def test_groups_attachments_and_rate_limit_are_rejected(db: Db, webhook: TelegramWebhook) -> None:
    _authorize(db, 3003)
    assert webhook.handle(SECRET, _update(6001, 3003, chat_type="group")).status == "rejected_unsupported"
    assert webhook.handle(SECRET, _update(6002, 3003, text=None)).status == "rejected_unsupported"
    statuses = [webhook.handle(SECRET, _update(6100 + i, 3003)).status for i in range(4)]
    assert statuses == ["accepted", "accepted", "accepted", "rejected_rate_limited"]


def test_review_resolution_and_revert_are_audited(db: Db) -> None:
    ingest, reviewer = db.create_user("ingest_service"), db.create_user("reviewer")
    case = db.rpc(ingest).call("review_open_case", {
        "p_case_type": "identity", "p_dedupe_key": unique("link"),
        "p_summary": "PL 249/2024 Senado y PL 312/2024 Cámara parecen el mismo expediente",
        "p_candidates": [{"senado": "249/2024", "camara": "312/2024", "basis": "similitud de título"}]})[0]
    rpc = db.rpc(reviewer)
    with pytest.raises(InvalidArgument):
        rpc.call("review_resolve", {"p_case_id": case["case_id"], "p_decision": "accept_candidate", "p_reason": ""})

    resolution = rpc.call("review_resolve", {
        "p_case_id": case["case_id"], "p_decision": "accept_candidate",
        "p_reason": "La gaceta 1234/2024 cita ambos números", "p_after": {"linked": True}})
    with pytest.raises(InvalidState):
        rpc.call("review_resolve", {"p_case_id": case["case_id"], "p_decision": "dismiss", "p_reason": "otra vez"})
    revert = rpc.call("review_revert", {"p_resolution_id": resolution, "p_reason": "La gaceta citaba otro expediente"})

    state = db.execute("select state, resolution_id from public.review_cases where id = %s", (case["case_id"],))[0]
    assert state == {"state": "open", "resolution_id": UUID(revert)}
    actions = db.execute("select action, reason from public.audit_log where target_id = %s order by id",
                         (case["case_id"],))
    assert [a["action"] for a in actions] == ["review.resolve", "review.revert"]
    jobs = db.execute("select count(*) as n from public.jobs where kind = 'maintenance.apply_resolution' "
                      "and idempotency_key in (%s, %s)", (resolution, revert))
    assert jobs[0]["n"] == 2


def test_retention_purges_expired_text(db: Db, webhook: TelegramWebhook) -> None:
    _authorize(db, 4004)
    webhook.handle(SECRET, _update(7001, 4004))
    db.execute("update public.query_runs set expires_at = now() - interval '1 day' where question_text is not null")
    purged = db.rpc(db.create_user("operator")).call("maintenance_purge_expired", {})[0]
    assert purged["questions"] >= 1
    assert db.execute("select count(*) as n from public.query_runs where question_text is not null")[0]["n"] == 0


def test_audit_log_is_immutable_until_retention_expires(db: Db) -> None:
    import psycopg

    recent = db.execute("insert into public.audit_log (action, target_type) values ('x', 'y') returning id")[0]["id"]
    old = db.execute("insert into public.audit_log (action, target_type, occurred_at) "
                     "values ('x', 'y', now() - interval '400 days') returning id")[0]["id"]
    for statement in ("delete from public.audit_log where id = %s", "update public.audit_log set reason = 'z' where id = %s"):
        with pytest.raises(psycopg.Error) as info:
            db.execute(statement, (recent,))
        assert info.value.sqlstate == "BC001"
    purged = db.rpc(db.create_user("operator")).call("maintenance_purge_expired", {})[0]
    assert purged["audit_rows"] == 1
    remaining = {r["id"] for r in db.execute("select id from public.audit_log where id in (%s, %s)", (recent, old))}
    assert remaining == {recent}


def test_lexical_and_vector_search_exclude_withdrawn_and_low_quality(db: Db) -> None:
    admin, query = db.create_user("admin"), db.create_user("query_service")
    active_source(db, admin)
    ids = db.execute(
        """
        with b as (insert into public.blobs (content_hash, byte_size) values (repeat('c', 64), 10),
                                                                         (repeat('d', 64), 10) returning content_hash),
             d as (insert into public.documents (document_key, document_type) values ('doc-a', 'texto_radicado'),
                                                                                  ('doc-b', 'ponencia') returning id, document_key)
        select (select id from d where document_key = 'doc-a') as doc_a, (select id from d where document_key = 'doc-b') as doc_b
        """)[0]
    rev = {}
    for key, doc, blob in (("a", ids["doc_a"], "c"), ("b", ids["doc_b"], "d")):
        rev[key] = db.execute(
            "insert into public.document_revisions (document_id, blob_hash, mime_type, byte_size) "
            "values (%s, repeat(%s, 64), 'application/pdf', 10) returning id", (doc, blob))[0]["id"]
    chunks = {}
    for key, text, quality in (("a", "El artículo 3 regula las tarifas del transporte público urbano", "accepted"),
                               ("b", "Las tarifas del transporte público se congelan por dos años", "accepted"),
                               ("c", "tarifas transporte ilegible", "review_required")):
        revision = rev["a"] if key != "b" else rev["b"]
        extraction = db.execute(
            "insert into public.extraction_runs (revision_id, extractor_version, config_hash, status, quality_status) "
            "values (%s, %s, 'cfg', 'succeeded', 'accepted') returning id", (revision, f"x-{key}"))[0]["id"]
        chunks[key] = db.execute(
            """insert into public.chunks (id, extraction_id, ordinal, text, text_hash, chunker_version, token_count,
                                          pdf_page_start, pdf_page_end, char_start, char_end, quality_status)
               values (gen_random_uuid(), %s, 0, %s, repeat('e', 64), 'v1', 8, 1, 1, 0, 10, %s) returning id""",
            (extraction, text, quality))[0]["id"]
    for key, vector in (("a", "[1,0,0]"), ("b", "[0.9,0.1,0]"), ("c", "[1,0,0]")):
        db.execute("insert into public.chunk_embeddings (chunk_id, model_id, model_version, dimensions, index_namespace, "
                   "embedding, state, indexed_at) values (%s, 'm', '1', 3, 'ns', %s::vector, 'indexed', now())",
                   (chunks[key], vector))

    rpc = db.rpc(query)
    lexical = [r["chunk_id"] for r in rpc.call("search_chunks_lexical", {"p_query": "tarifas transporte"})]
    assert set(lexical) == {str(chunks["a"]), str(chunks["b"])}
    vector = [r["chunk_id"] for r in rpc.call("search_chunks_vector", {
        "p_embedding": [1, 0, 0], "p_model_id": "m", "p_model_version": "1"})]
    assert vector == [str(chunks["a"]), str(chunks["b"])]

    db.execute("update public.document_revisions set withdrawn_at = now(), withdrawal_reason = 'retirado en origen' "
               "where id = %s", (rev["b"],))
    lexical = [r["chunk_id"] for r in rpc.call("search_chunks_lexical", {"p_query": "tarifas transporte"})]
    assert lexical == [str(chunks["a"])]
