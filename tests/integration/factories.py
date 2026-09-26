"""Datos de prueba reutilizables."""

from __future__ import annotations

import uuid
from uuid import UUID

from tests.integration.support import Db

_counter = iter(range(10, 10_000))


def new_source(db: Db, *, authority: str = "primary", domains: tuple[str, ...] = ("datos.example.gov.co",)) -> UUID:
    """Fuente candidata con los campos operativos completos (aún sin política ni cobertura)."""
    code = f"SRC-{next(_counter):04d}"
    row = db.execute(
        """
        insert into public.sources (code, name, authority, phase, base_url, allowed_domains, adapter,
                                    adapter_version, owner)
        values (%s, %s, %s, 'mvp', %s, %s, 'fake', '1.0.0', 'equipo-datos')
        returning id
        """,
        (code, f"Fuente {code}", authority, f"https://{domains[0]}/", list(domains)),
    )
    return row[0]["id"]


def activate_source(db: Db, source_id: UUID, admin_id: UUID, *, download: bool = True) -> None:
    rpc = db.rpc(admin_id)
    allowed = "allowed" if download else "denied"
    rpc.call("admin_add_source_policy", {
        "p_source_id": source_id,
        "p_capture_metadata": "allowed",
        "p_download_files": allowed,
        "p_retain_content": allowed,
        "p_generate_derivatives": allowed,
        "p_index_content": allowed,
        "p_redistribute": "unknown",
        "p_reviewer": "revisor-legal",
        "p_evidence_url": "https://datos.example.gov.co/terminos",
        "p_retention_policy": "conservar mientras la fuente lo permita",
        "p_reason": "perfil validado en descubrimiento",
    })
    db.execute(
        "insert into public.coverage_scopes (source_id, object_type, status) values (%s, 'project', 'in_progress')",
        (source_id,),
    )
    rpc.call("admin_set_source_state", {"p_source_id": source_id, "p_state": "active",
                                        "p_reason": "descubrimiento completado"})


def active_source(db: Db, admin_id: UUID, **kwargs: object) -> UUID:
    source_id = new_source(db)
    activate_source(db, source_id, admin_id, **kwargs)  # type: ignore[arg-type]
    return source_id


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
