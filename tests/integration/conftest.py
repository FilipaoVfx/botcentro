"""Base de datos de pruebas: plantilla con el shim de InsForge y todas las migraciones.

Variables: BOTCENTRO_TEST_PG_HOST (directorio del socket o host), BOTCENTRO_TEST_PG_PORT y
BOTCENTRO_TEST_PG_USER (superusuario). Sin ellas, las pruebas marcadas `db` se omiten.
Cada módulo de prueba recibe una base nueva clonada de la plantilla.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from tests.integration.support import Db

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = "botcentro_test_template"


def _base_conninfo(dbname: str) -> str | None:
    host = os.environ.get("BOTCENTRO_TEST_PG_HOST")
    if not host:
        return None
    port = os.environ.get("BOTCENTRO_TEST_PG_PORT", "5432")
    user = os.environ.get("BOTCENTRO_TEST_PG_USER", "postgres")
    return f"host={host} port={port} user={user} dbname={dbname}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _base_conninfo("postgres") is not None:
        return
    skip = pytest.mark.skip(reason="BOTCENTRO_TEST_PG_HOST no definido")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def template_db() -> str:
    conninfo = _base_conninfo("postgres")
    assert conninfo is not None
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(f"drop database if exists {TEMPLATE}")
        conn.execute(f"create database {TEMPLATE}")
    template = _base_conninfo(TEMPLATE)
    assert template is not None
    with psycopg.connect(template, autocommit=True) as conn:
        conn.execute((ROOT / "ops/sql/plataforma.sql").read_text())  # misma plataforma que producción (DEC-21)
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        with psycopg.connect(template) as conn:  # una transacción por migración, como InsForge
            conn.execute("set role project_admin")
            conn.execute(migration.read_text())
    return TEMPLATE


@pytest.fixture(scope="module")
def db(template_db: str) -> Iterator[Db]:
    name = f"botcentro_test_{uuid.uuid4().hex[:10]}"
    admin = _base_conninfo("postgres")
    assert admin is not None
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"create database {name} template {template_db}")
    conninfo = _base_conninfo(name)
    assert conninfo is not None
    database = Db(conninfo)
    try:
        yield database
    finally:
        database.close()
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f"drop database if exists {name} with (force)")
