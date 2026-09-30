"""Plataforma autoalojada (DEC-21): acceso al panel por Telegram, cuentas de servicio y migraciones."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from botcentro.db.migrate import MigrationError, migrate
from botcentro.insforge.client import InsForgeError
from botcentro.panel.local_auth import LocalAuth, parse_operators
from tests.integration.support import Db

pytestmark = pytest.mark.db


@pytest.fixture()
def pool(db: Db):
    """Como en producción: el rol de aplicación sin privilegios propios (no el superusuario)."""
    app = re.sub(r"user=\S+", "user=botcentro_app", db.conninfo)
    p = ConnectionPool(app, min_size=1, max_size=2, open=True, kwargs={"row_factory": dict_row})
    yield p
    p.close()


def test_panel_code_goes_to_operator_telegram_and_reads_as_that_user(db: Db, pool) -> None:
    admin = db.create_user("admin", email="operadora@example.org")
    sent: list[tuple[int, str]] = []
    now = [1000.0]
    auth = LocalAuth(pool, parse_operators("operadora@example.org=555, rota"), lambda c, t: sent.append((c, t)),
                     clock=lambda: now[0])
    auth.send_code("desconocido@example.org")
    assert sent == []  # correo no operador: sin envío y sin error
    auth.send_code("Operadora@Example.org")
    assert sent[0][0] == 555
    code = re.search(r"\b(\d{6})\b", sent[0][1]).group(1)
    with pytest.raises(InsForgeError):
        auth.verify_code("operadora@example.org", "000000" if code != "000000" else "111111")
    body = auth.verify_code("operadora@example.org", code)
    assert body["user"]["id"] == str(admin)
    caps = auth.rpc(body["accessToken"], "ops_capabilities", {})
    assert caps["is_admin"] or "admin" in caps["roles"]
    with pytest.raises(InsForgeError):  # un solo uso
        auth.verify_code("operadora@example.org", code)
    now[0] += 13 * 3600
    with pytest.raises(InsForgeError):  # el token vence
        auth.rpc(body["accessToken"], "ops_capabilities", {})


def test_code_attempts_and_expiry_are_limited(db: Db, pool) -> None:
    db.create_user("operator", email="op2@example.org")
    sent: list[tuple[int, str]] = []
    now = [0.0]
    auth = LocalAuth(pool, {"op2@example.org": 7}, lambda c, t: sent.append((c, t)), clock=lambda: now[0])
    auth.send_code("op2@example.org")
    code = re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(InsForgeError):
            auth.verify_code("op2@example.org", wrong)
    with pytest.raises(InsForgeError):  # agotó los intentos aunque ahora acierte
        auth.verify_code("op2@example.org", code)
    auth.send_code("op2@example.org")
    code = re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)
    now[0] += 601
    with pytest.raises(InsForgeError):
        auth.verify_code("op2@example.org", code)


def test_service_accounts_resolve_only_with_service_roles(db: Db) -> None:
    ingest = db.create_user("ingest_service", email="ingesta@svc.local")
    db.create_user("admin", email="jefa@example.org")
    rows = db.execute("select public.service_user_id('INGESTA@svc.local') a, public.service_user_id('jefa@example.org') b")
    assert rows[0]["a"] == ingest and rows[0]["b"] is None


def test_migration_runner_is_ordered_idempotent_and_immutable(db: Db, tmp_path: Path) -> None:
    (tmp_path / "20990101000001_uno.sql").write_text("create table public.prueba_mig (id int);")
    (tmp_path / "20990101000002_dos.sql").write_text("insert into public.prueba_mig values (1);")
    logs: list[str] = []
    assert migrate(db.conninfo, tmp_path, log=logs.append) == 2
    assert migrate(db.conninfo, tmp_path, log=logs.append) == 0
    owner = db.execute("select tableowner from pg_tables where tablename = 'prueba_mig'")[0]["tableowner"]
    assert owner == "project_admin"
    (tmp_path / "20990101000001_uno.sql").write_text("create table public.prueba_mig (id bigint);")
    with pytest.raises(MigrationError):
        migrate(db.conninfo, tmp_path)


def test_import_copies_common_columns_and_matches_counts(db: Db) -> None:
    """Importación desde el respaldo de InsForge: mismos conteos, identidades y secuencias ajustadas."""
    import uuid as _uuid

    import psycopg as _pg

    from botcentro.db.import_insforge import import_all
    from tests.integration.conftest import TEMPLATE

    user = db.create_user("ingest_service", email="svc@import.local")
    src_id = db.execute("insert into public.sources (code, name, authority, phase, base_url, allowed_domains, supported_objects) "
                        "values ('SRC-99', 'prueba', 'primary', 'mvp', 'https://x.gov.co', '{x.gov.co}', '{project}') "
                        "returning id")[0]["id"]
    with db.admin() as conn:  # hija de sources sin montar su captura: basta para la regresión del TRUNCATE
        conn.execute("set session_replication_role = replica")
        conn.execute("insert into public.observations (observation_key, source_id, first_snapshot_id, authority, subject_type, "
                     "subject_ref, predicate, value_json, first_observed_at, last_observed_at, parser_version) values "
                     "(repeat('a', 64), %s, gen_random_uuid(), 'primary', 'project', 'p1', 'p', '{}', now(), now(), 'v1')", (src_id,))
    db.execute("alter table auth.users add column if not exists insforge_only text")  # columna solo del origen
    target_name = f"botcentro_import_{_uuid.uuid4().hex[:8]}"
    host = db.conninfo  # "host=... port=... user=... dbname=..."
    admin_kv = host.rsplit("dbname=", 1)[0] + "dbname=postgres"
    with _pg.connect(admin_kv, autocommit=True) as conn:
        conn.execute(f"create database {target_name} template {TEMPLATE}")
    target = host.rsplit("dbname=", 1)[0] + f"dbname={target_name}"
    try:
        report = import_all(target, db.conninfo, log=lambda _m: None)
        assert report["auth.users"][0] == report["auth.users"][1] >= 1
        # Regresión: las tablas hijas (observations → sources) no se vacían al importar la madre después.
        with _pg.connect(target) as conn:
            final = conn.execute("select (select count(*) from public.sources), (select count(*) from public.observations)").fetchone()
        assert final == (report["public.sources"][0], report["public.observations"][0]) and final[1] >= 1
        with _pg.connect(target) as conn:
            role = conn.execute("select role from public.app_roles where user_id = %s", (user,)).fetchone()
            assert role == ("ingest_service",)
    finally:
        with _pg.connect(admin_kv, autocommit=True) as conn:
            conn.execute(f"drop database if exists {target_name} with (force)")


def test_records_subset_matches_cli_usage(db: Db) -> None:
    """select con filtros eq, orden, límite y desplazamiento, bajo RLS del usuario (como PostgREST)."""
    svc = db.create_user("ingest_service", email="svc2@records.local")
    client = db.rpc(svc)
    rows = client.select("corporations", {"select": "id,code", "order": "code", "limit": "1", "offset": "1"})
    assert len(rows) <= 1 and all(set(r) == {"id", "code"} for r in rows)
    with pytest.raises(InsForgeError):
        client.select("corporations", {"code": "like.x"})
