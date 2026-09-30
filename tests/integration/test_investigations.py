"""Investigaciones de punta a punta contra SQL real, con datos sintéticos (investigaciones §21).

Cadena: Socrata simulado con los metadatos reales versionados → SocrataConnector → IngestionRunner
→ observaciones → normalizadores → afirmaciones y casos → flujo editorial → lecturas públicas →
outbox → seguimientos. Ninguna identidad ni caso corresponde a personas reales.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlparse

import pytest

from botcentro.connectors.base import Cursor
from botcentro.domain.hashing import sha256_hex
from botcentro.errors import FailureKind, FetchError
from botcentro.http.fetcher import Fetched
from botcentro.ingest.runner import IngestionRunner
from botcentro.ingest.store import IngestStore
from botcentro.insforge.client import InsForgeError
from botcentro.investigations.connector import SocrataConnector
from botcentro.investigations.datasets import SPECS
from botcentro.sources.policy import Permission, UsageProfile
from botcentro.storage.objects import LocalObjectStore
from botcentro.telegram.security import pseudonymize_user
from tests.integration.support import Db

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "investigaciones"
SEED = Path(__file__).resolve().parents[2] / "seeds" / "fuentes_investigaciones.sql"
KEY = b"i" * 32
BOT_ID, TG_A, TG_B = 555, 7001, 7002


def fetched(content: bytes) -> Fetched:
    return Fetched("u", "u", 200, content, sha256_hex(content), "application/json", "application/json", None, None,
                   datetime.now(timezone.utc))


class FakeSocrata:
    """Servidor Socrata en memoria: metadatos reales y filas sintéticas paginadas por `:id`."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}
        self.requests: list[str] = []
        self.fail_urls: set[str] = set()

    def fetch(self, url, *, method="GET", data=None, accept_mimes=None, **kw):
        self.requests.append(url)
        parsed = urlparse(url)
        if parsed.path.startswith("/api/views/"):
            dataset = parsed.path.rsplit("/", 1)[1].removesuffix(".json")
            return fetched((FIXTURES / f"meta-{dataset}.json").read_bytes())
        dataset = parsed.path.rsplit("/", 1)[1].removesuffix(".json")
        query = parse_qs(parsed.query)
        where = query.get("$where", [""])[0]
        after = re.search(r":id > '([^']*)'", where)
        limit = int(query.get("$limit", ["1000"])[0])
        rows = sorted(self.rows.get(dataset, []), key=lambda r: r[":id"])
        if after:
            rows = [r for r in rows if r[":id"] > after.group(1)]
        return fetched(json.dumps(rows[:limit]).encode())


def run_source(db: Db, ingest_user, fake: FakeSocrata, adapter: str, scope: dict, tmp_path, *, reparse=False, previous=None,
               **limits):
    spec = SPECS[adapter]
    source = db.execute("select id from public.sources where code = %s", (spec.code,))[0]["id"]
    connector = SocrataConnector(fake, spec, identity_key=KEY, pause_seconds=0, **limits)  # type: ignore[arg-type]
    connector.validate_source({})
    runner = IngestionRunner(source_id=source, connector=connector, store=IngestStore(db.rpc(ingest_user)),
                             objects=LocalObjectStore(tmp_path), profile=UsageProfile(*[Permission.ALLOWED] * 6,
                             reviewed_at=datetime.now(timezone.utc), reviewer="r"), reparse=reparse)
    return runner.run(mode="backfill", scope=scope, previous_cursor=previous)


def normalize(db: Db, ingest_user, step: str) -> None:
    rpc = db.rpc(ingest_user)
    for _ in range(20):  # un paso que no termina es un defecto (observaciones repetidas reprocesadas sin fin)
        counts = rpc.call("normalize_investigations", {"p_step": step, "p_limit": 50})
        if not any(v for k, v in counts.items() if k != "step"):
            return
    raise AssertionError(f"el paso {step} no converge")


DIVIPOLA = [
    {":id": "row-001", "cod_dpto": "18", "dpto": "CAQUETÁ", "cod_mpio": "18001", "nom_mpio": "FLORENCIA", "tipo_municipio": "Municipio"},
    {":id": "row-002", "cod_dpto": "05", "dpto": "ANTIOQUIA", "cod_mpio": "05664", "nom_mpio": "SAN PEDRO DE LOS MILAGROS", "tipo_municipio": "Municipio"},
    {":id": "row-003", "cod_dpto": "70", "dpto": "SUCRE", "cod_mpio": "70717", "nom_mpio": "SAN PEDRO", "tipo_municipio": "Municipio"},
    {":id": "row-004", "cod_dpto": "76", "dpto": "VALLE DEL CAUCA", "cod_mpio": "76670", "nom_mpio": "SAN PEDRO", "tipo_municipio": "Municipio"},
    {":id": "row-005", "cod_dpto": "76", "dpto": "VALLE DEL CAUCA", "cod_mpio": "76109", "nom_mpio": "BUENAVENTURA", "tipo_municipio": "Municipio"},
]


def siri_row(rid: str, siri: str, doc: str, first: str, last: str, muni="FLORENCIA", dept="CAQUETA",
             sanction="SUSPENSION") -> dict:
    return {":id": rid, "numero_siri": siri, "tipo_inhabilidad": "DISCIPLINARIO", "calidad_persona": "SERVIDOR PUBLICO",
            "tipo_identificacion": "1", "nombre_tipo_identificacion": "CÉDULA DE CIUDADANÍA",
            "numero_identificacion": doc + "     ", "primer_apellido": last, "segundo_apellido": "NA",
            "primer_nombre": first, "segundo_nombre": "", "cargo": "SECRETARIO DE DESPACHO",
            "lugar_hechos_departamento": dept, "lugar_hechos_municipio": muni, "sanciones": sanction,
            "duracion_anos": "", "duracion_mes": "3", "duracion_dias": "", "providencia": "PRIMERA",
            "autoridad": "PROCURADURIA PROVINCIAL SINTETICA", "fecha_efectos_juridicos": "15/03/2025",
            "numero_proceso": "SYN-2024-001", "entidad_sancionado": "ALCALDIA SINTETICA",
            "entidad_departamento": dept, "entidad_municipio": muni}


def contract_row(rid: str, native: str, status: str, value: str, signed: str | None, doc_type="NIT", doc="0800123456") -> dict:
    return {":id": rid, "nombre_entidad": "ALCALDIA SINTETICA DE FLORENCIA", "nit_entidad": "0891180009",
            "departamento": "Caquetá", "ciudad": "Florencia", "orden": "Territorial", "sector": "Servicio Público",
            "id_contrato": native, "estado_contrato": status, "descripcion_del_proceso": "SUMINISTRO SINTETICO",
            "tipo_de_contrato": "Suministros", "modalidad_de_contratacion": "Contratación directa",
            **({"fecha_de_firma": signed} if signed else {}),
            "tipodocproveedor": doc_type, "documento_proveedor": doc, "proveedor_adjudicado": "PROVEEDOR SINTETICO S.A.S.",
            "valor_del_contrato": value, "valor_pagado": "0", "urlproceso": {"url": f"https://community.secop.gov.co/{native}"},
            "ultima_actualizacion": "2026-09-01T00:00:00.000"}


@pytest.fixture(scope="module")
def world(db: Db, tmp_path_factory):
    db.execute(SEED.read_text())
    users = {r: db.create_user(r) for r in ("admin", "ingest_service", "query_service", "editor", "reviewer")}
    users["editor2"] = db.create_user("editor", "reviewer")
    fake = FakeSocrata()
    fake.rows["gdxc-w37w"] = DIVIPOLA
    tmp = tmp_path_factory.mktemp("objs")
    assert run_source(db, users["ingest_service"], fake, "divipola", {}, tmp).status == "succeeded"
    normalize(db, users["ingest_service"], "territories")
    fake.rows["iaeu-rcn6"] = [siri_row("s-1", "900001", "10000001", "JUAN", "PEREZ"),
                              siri_row("s-2", "900002", "10000002", "JUAN", "PEREZ"),  # homónimos (T-19)
                              siri_row("s-3", "900001", "10000001", "JUAN", "PEREZ", sanction="MULTA"),  # otra sanción
                              {**siri_row("s-1", "900001", "10000001", "JUAN", "PEREZ"), ":id": "s-4"}]  # fila repetida
    run_source(db, users["ingest_service"], fake, "siri", {"territories": [{"departamento": "Caquetá", "municipio": "Florencia"}]}, tmp)
    normalize(db, users["ingest_service"], "siri")
    fake.rows["jbjy-vk9h"] = [contract_row("c-1", "CO1.SYN.1", "En ejecución", "-12.50", "2026-02-01T00:00:00.000"),
                              contract_row("c-0", "CO1.SYN.1", "En ejecución", "-12.50", "2026-02-01T00:00:00.000"),
                              contract_row("c-2", "CO1.SYN.2", "Borrador", "1000", None, "Cédula de Ciudadanía", "00012345")]
    run_source(db, users["ingest_service"], fake, "secop2_contracts",
               {"territories": [{"departamento": "Caquetá", "municipio": "Florencia"}], "from": "2026-01-01"}, tmp)
    normalize(db, users["ingest_service"], "contracts")
    db.rpc(users["admin"]).call("admin_authorize_telegram", {"p_bot_id": BOT_ID, "p_user_hash": pseudonymize_user(KEY, BOT_ID, TG_A),
                                                             "p_chat_id": TG_A, "p_label": "a", "p_reason": "prueba integración"})
    db.rpc(users["admin"]).call("admin_authorize_telegram", {"p_bot_id": BOT_ID, "p_user_hash": pseudonymize_user(KEY, BOT_ID, TG_B),
                                                             "p_chat_id": TG_B, "p_label": "b", "p_reason": "prueba integración"})
    return {"db": db, "users": users, "fake": fake, "tmp": tmp}


def rpc(world, who):
    return world["db"].rpc(world["users"][who])


def one(world, sql, *params):
    return world["db"].execute(sql, params or None)[0]


# -- fuentes, esquema y normalización ------------------------------------------------------------

def test_territories_and_homonyms(world) -> None:
    """T-36: municipios homónimos se presentan con su departamento."""
    found = rpc(world, "query_service").call("public_territory_search", {"p_text": "San Pedro"})
    departments = {t["department"] for t in found if t["name"] == "SAN PEDRO"}
    assert departments == {"SUCRE", "VALLE DEL CAUCA"}


def test_siri_records_are_pending_claims_never_published_automatically(world) -> None:
    """EVI-10 / T-47: la sanción queda como afirmación pendiente; nada de ella es visible públicamente."""
    counts = one(world, "select count(*) filter (where state = 'pending_review') pending, "
                        "count(*) filter (where state = 'published') published from public.claims where origin = 'system_ingest'")
    assert counts == {"pending": 2, "published": 0}
    q = rpc(world, "query_service")
    assert q.call("public_proceedings_list", {"p_filters": {}, "p_limit": 50})["known_total"] == 0
    assert q.call("public_actor_search", {"p_tokens": ["juan", "perez"], "p_limit": 8}) == []


def test_repeated_siri_rows_group_into_one_record(world) -> None:
    """Regresión: varias filas del mismo número SIRI ⇒ un expediente, una afirmación por persona, una actuación por sanción."""
    procs = world["db"].execute("select p.source_identifier, count(distinct e.description) sanciones, count(e.id) eventos "
                                "from public.proceedings p join public.proceeding_events e on e.proceeding_id = p.id "
                                "where p.system = 'SIRI' group by 1 order by 1")
    assert [(p["source_identifier"], p["sanciones"], p["eventos"]) for p in procs] == [("900001", 2, 2), ("900002", 1, 1)]
    evidence = one(world, "select count(*) n from public.claim_evidence ce join public.participations pa on pa.claim_id = ce.claim_id "
                          "join public.proceedings p on p.id = pa.proceeding_id where p.source_identifier = '900001'")
    assert evidence["n"] == 1  # la evidencia es la página capturada: las filas repetidas no la duplican


def test_homonyms_are_two_actors_and_documents_are_never_stored(world) -> None:
    """T-19 y SEG-02: dos documentos distintos ⇒ dos actores aunque el nombre coincida; sin cédula en claro."""
    actors = world["db"].execute("select a.id, i.value_hmac, i.value_masked from public.actors a "
                                 "join public.actor_identifiers i on i.actor_id = a.id where a.display_name = 'JUAN PEREZ'")
    assert len(actors) == 2 and all(a["value_masked"].endswith(("001", "002")) for a in actors)
    dump = json.dumps(world["db"].execute("select value_json from public.observations where predicate = 'siri_record'"))
    assert "10000001" not in dump and "10000002" not in dump


def test_contracts_keep_sign_zeros_and_unsigned_contracts(world) -> None:
    """T-05, T-17, T-18: -12.50 conserva el signo; el NIT conserva el cero inicial; el contrato sin firma existe."""
    rows = world["db"].execute("select c.native_id, v.value_initial, v.signed_on from public.contracts c "
                               "join public.contract_versions v on v.id = c.current_version_id order by c.native_id")
    assert [str(r["value_initial"]) for r in rows] == ["-12.50", "1000.00"]
    assert rows[1]["signed_on"] is None
    nit = one(world, "select value_public from public.actor_identifiers where issuer = 'DIAN' and value_public like '0891%'")
    assert nit["value_public"] == "0891180009"
    person = one(world, "select value_hmac, value_masked, value_public from public.actor_identifiers where issuer = 'SECOP'")
    assert person["value_public"] is None and person["value_masked"].endswith("345")


def test_repeated_observation_creates_no_version_and_changes_do(world) -> None:
    """T-14, T-15 (contratos), T-16: repetir no crea versión; un cambio sí; una fila ausente no borra."""
    db, ingest = world["db"], world["users"]["ingest_service"]
    scope = {"territories": [{"departamento": "Caquetá", "municipio": "Florencia"}], "from": "2026-01-01"}
    run_source(db, ingest, world["fake"], "secop2_contracts", scope, world["tmp"], reparse=True)
    normalize(db, ingest, "contracts")
    assert one(world, "select count(*) n from public.contract_versions")["n"] == 2
    world["fake"].rows["jbjy-vk9h"] = [contract_row("c-1", "CO1.SYN.1", "Modificado", "-12.50", "2026-02-01T00:00:00.000")]
    run_source(db, ingest, world["fake"], "secop2_contracts", scope, world["tmp"])
    normalize(db, ingest, "contracts")
    assert one(world, "select count(*) n from public.contract_versions")["n"] == 3
    assert one(world, "select count(*) n from public.contracts")["n"] == 2  # CO1.SYN.2 ausente no se borra
    current = one(world, "select v.status_original from public.contracts c join public.contract_versions v "
                         "on v.id = c.current_version_id where c.native_id = 'CO1.SYN.1'")
    assert current["status_original"] == "Modificado"


# -- carga asistida y evidencia -----------------------------------------------------------------

def _document(world, url: str, text: str, *, quality="accepted"):
    body = text.encode()
    return rpc(world, "editor").call("editor_register_document", {
        "p_source_code": "SRC-22", "p_url": url, "p_final_url": url, "p_title": "Boletín sintético", "p_document_type": "otro",
        "p_content_hash": hashlib.sha256(body).hexdigest(), "p_byte_size": len(body), "p_mime": "application/pdf",
        "p_object_key": None, "p_pages": [{"page": 1, "text": text, "method": "native", "quality": quality}],
        "p_extractor_version": "pdf-text-1"})


def test_same_url_new_bytes_is_a_new_version_and_quarantine_blocks_citation(world) -> None:
    """T-15 y T-28."""
    first = _document(world, "https://cortesuprema.gov.co/syn/boletin-1.pdf", "Texto sintético versión uno de la decisión.")
    same = _document(world, "https://cortesuprema.gov.co/syn/boletin-1.pdf", "Texto sintético versión uno de la decisión.")
    second = _document(world, "https://cortesuprema.gov.co/syn/boletin-1.pdf", "Texto sintético versión DOS de la decisión.")
    assert first["new_version"] and not same["new_version"] and second["new_version"]
    assert second["revision_id"] != first["revision_id"]
    bad = _document(world, "https://cortesuprema.gov.co/syn/ilegible.pdf", "", quality="review_required")
    with pytest.raises(InsForgeError) as err:
        rpc(world, "editor").call("editor_create_evidence_passage", {"p_revision_id": bad["revision_id"], "p_page": 1,
                                                                      "p_char_start": 0, "p_char_end": 1})
    assert err.value.sqlstate in ("BC422", "22023")


# -- flujo editorial, casos y lecturas públicas ---------------------------------------------------

@pytest.fixture(scope="module")
def case(world):
    """Caso sintético con dos expedientes: uno archivado y otro en investigación (T-24)."""
    ed, rv = rpc(world, "editor"), rpc(world, "reviewer")
    text = "La autoridad sintética ordenó el archivo del expediente A y abrió investigación en el expediente B contra la persona X."
    doc = _document(world, "https://cortesuprema.gov.co/syn/decision.pdf", text)
    ev = ed.call("editor_create_evidence_passage", {"p_revision_id": doc["revision_id"], "p_page": 1,
                                                    "p_char_start": 0, "p_char_end": 60})
    ev2 = ed.call("editor_create_evidence_passage", {"p_revision_id": doc["revision_id"], "p_page": 1,
                                                     "p_char_start": 61, "p_char_end": len(text)})

    def approved_claim(statement, **kw):
        cid = ed.call("editor_create_claim", {"p_claim_type": kw.get("t", "other"), "p_statement": statement,
                                              "p_subject_type": kw.get("st"), "p_subject_id": kw.get("sid"),
                                              "p_object_type": kw.get("ot"), "p_object_id": kw.get("oid"),
                                              "p_value": {}, "p_sensitive": True, "p_evidence_ids": kw.get("ev", [ev])})
        version = ed.call("editor_submit_claim", {"p_claim_id": cid, "p_expected_version": 1})
        assert rv.call("reviewer_decide_claim", {"p_claim_id": cid, "p_expected_version": version, "p_decision": "approve",
                                                 "p_reason": "verificado en la fuente"}) == "published"
        return cid

    florencia = one(world, "select id from public.territories where code = '18001'")["id"]
    status_a = approved_claim("El expediente A fue archivado según la decisión sintética.")
    status_b = approved_claim("El expediente B está en investigación según la decisión sintética.", ev=[ev2])
    proc_a = ed.call("editor_upsert_proceeding", {"p_system": "CSJ-SYN", "p_source_identifier": "A-2025", "p_authority": "Autoridad sintética",
                                                  "p_jurisdiction": "penal", "p_radicado": "A-2025", "p_official_url": None,
                                                  "p_status_original": "Archivo", "p_status_normalized": "archivado",
                                                  "p_finality": "en_firme", "p_territory_id": florencia, "p_claim_id": status_a})
    proc_b = ed.call("editor_upsert_proceeding", {"p_system": "CSJ-SYN", "p_source_identifier": "B-2026", "p_authority": "Autoridad sintética",
                                                  "p_jurisdiction": "disciplinaria", "p_radicado": "B-2026", "p_official_url": None,
                                                  "p_status_original": "Investigación", "p_status_normalized": "investigacion",
                                                  "p_finality": "recurrida", "p_territory_id": florencia, "p_claim_id": status_b})
    person = ed.call("editor_create_actor", {"p_actor_type": "persona", "p_display_name": "Persona Sintética X", "p_territory_id": florencia})
    witness = ed.call("editor_create_actor", {"p_actor_type": "persona", "p_display_name": "Testigo Sintético", "p_territory_id": None})
    party = ed.call("editor_create_actor", {"p_actor_type": "partido", "p_display_name": "Partido Sintético", "p_territory_id": None})
    alcaldia = ed.call("editor_create_actor", {"p_actor_type": "entidad_publica", "p_display_name": "Alcaldía de Florencia (sintética)",
                                                "p_territory_id": florencia})
    case_id = ed.call("editor_create_case", {"p_slug": "caso-sintetico", "p_title": "Caso sintético de contratación",
                                             "p_summary": "Agrupación editorial de dos expedientes sintéticos para pruebas.",
                                             "p_scope": "Pruebas", "p_coverage_note": "Datos sintéticos", "p_topics": ["contratacion"],
                                             "p_aliases": [], "p_territory_ids": [florencia], "p_evidence_ids": [ev],
                                             "p_featured": True, "p_featured_reason": "Multiplicidad de expedientes"})
    for proc in (proc_a, proc_b):
        link = approved_claim("El expediente pertenece al caso sintético.", t="case_link", st="proceeding", sid=proc,
                              ot="case", oid=case_id)
        ed.call("editor_link", {"p_kind": "case_proceeding", "p_a": case_id, "p_b": proc, "p_role_original": None,
                                "p_role_normalized": None, "p_text": "Mencionado en la decisión", "p_starts_on": None,
                                "p_ends_on": None, "p_claim_id": link})
    part = approved_claim("La persona X figura como investigada en B.", t="participation", st="actor", sid=person,
                          ot="proceeding", oid=proc_b)
    ed.call("editor_link", {"p_kind": "participation", "p_a": person, "p_b": proc_b, "p_role_original": "investigado",
                            "p_role_normalized": "investigado", "p_text": None, "p_starts_on": None, "p_ends_on": None,
                            "p_claim_id": part})
    wit = approved_claim("El testigo declaró en B.", t="participation", st="actor", sid=witness, ot="proceeding", oid=proc_b)
    ed.call("editor_link", {"p_kind": "participation", "p_a": witness, "p_b": proc_b, "p_role_original": "testigo",
                            "p_role_normalized": "testigo", "p_text": None, "p_starts_on": None, "p_ends_on": None, "p_claim_id": wit})
    aff = approved_claim("X estuvo afiliada al partido sintético en 2024.", t="affiliation", st="actor", sid=person,
                         ot="actor", oid=party)
    ed.call("editor_link", {"p_kind": "affiliation", "p_a": person, "p_b": party, "p_role_original": None,
                            "p_role_normalized": "partido", "p_text": None, "p_starts_on": "2024-01-01", "p_ends_on": "2024-12-31",
                            "p_claim_id": aff})
    for entity, start, end in ((alcaldia, "2020-01-01", "2023-12-31"), (party, "2024-01-01", None)):
        pos = approved_claim("Cargo sintético verificado.", t="position", st="actor", sid=person)
        ed.call("editor_link", {"p_kind": "position", "p_a": person, "p_b": entity, "p_role_original": "Secretaria",
                                "p_role_normalized": None, "p_text": None, "p_starts_on": start, "p_ends_on": end, "p_claim_id": pos})
    same_day = "2026-03-10"
    for proc in (proc_a, proc_b):  # T-27: dos expedientes, misma fecha
        evc = approved_claim("Actuación sintética del día.", t="event", st="proceeding", sid=proc, ev=[ev, ev2])  # T-26: dos fuentes
        ed.call("editor_link", {"p_kind": "event", "p_a": proc, "p_b": None, "p_role_original": None,
                                "p_role_normalized": "actuacion", "p_text": "Actuación sintética", "p_starts_on": same_day,
                                "p_ends_on": None, "p_claim_id": evc})
    revision = one(world, "select id from public.case_revisions where case_id = %s", case_id)["id"]
    ed.call("editor_submit_case_revision", {"p_revision_id": revision})
    return {"case_id": case_id, "revision": revision, "proc_a": proc_a, "proc_b": proc_b, "person": person, "party": party,
            "ev": ev, "ev2": ev2, "approved_claim": approved_claim, "florencia": florencia}


def test_draft_case_is_invisible_until_published(world, case) -> None:
    """T-47: un borrador no aparece en la API ni en la búsqueda."""
    q = rpc(world, "query_service")
    assert q.call("public_case", {"p_case_id": case["case_id"]}) is None
    assert q.call("public_cases_list", {"p_filters": {}, "p_limit": 10})["known_total"] == 0


def test_author_cannot_approve_own_sensitive_work_in_production(world, case) -> None:
    """T-31: en producción un editor no aprueba su propia atribución sensible (regla de dominio)."""
    ed2 = rpc(world, "editor2")
    cid = ed2.call("editor_create_claim", {"p_claim_type": "other", "p_statement": "Afirmación sintética propia.",
                                           "p_subject_type": None, "p_subject_id": None, "p_object_type": None,
                                           "p_object_id": None, "p_value": {}, "p_sensitive": True, "p_evidence_ids": [case["ev"]]})
    version = ed2.call("editor_submit_claim", {"p_claim_id": cid, "p_expected_version": 1})
    with pytest.raises(InsForgeError) as err:
        ed2.call("reviewer_decide_claim", {"p_claim_id": cid, "p_expected_version": version, "p_decision": "approve",
                                           "p_reason": "intento propio"})
    assert err.value.sqlstate == "42501"


def test_claim_without_evidence_cannot_be_submitted(world) -> None:
    """T-30: rechazo de dominio, no solo de interfaz."""
    ed = rpc(world, "editor")
    cid = ed.call("editor_create_claim", {"p_claim_type": "other", "p_statement": "Sin evidencia sintética.", "p_subject_type": None,
                                          "p_subject_id": None, "p_object_type": None, "p_object_id": None, "p_value": {},
                                          "p_sensitive": True, "p_evidence_ids": []})
    with pytest.raises(InsForgeError) as err:
        ed.call("editor_submit_claim", {"p_claim_id": cid, "p_expected_version": 1})
    assert err.value.sqlstate == "BC422"


def test_publish_case_and_read_branches_timeline_and_actors(world, case) -> None:
    """T-24, T-25, T-21..T-23, T-26, T-27, T-58 y outbox en la misma transacción (SUB-03)."""
    rv, q = rpc(world, "reviewer"), rpc(world, "query_service")
    assert rv.call("reviewer_decide_case_revision", {"p_revision_id": case["revision"], "p_expected_latest": 1,
                                                     "p_decision": "approve", "p_reason": "caso verificado"}) == "published"
    assert one(world, "select count(*) n from public.outbox_events where event_type = 'case_published'")["n"] == 1
    world["db"].execute("update public.proceedings set last_verified_at = now() - interval '90 days' where id = %s",
                        (case["proc_a"],))
    card = q.call("public_case", {"p_case_id": case["case_id"]})
    branches = {b["radicado"]: b for b in card["branches"]}
    assert branches["A-2025"]["status"] == "archivado" and branches["B-2026"]["status"] == "investigacion"
    assert branches["A-2025"]["freshness"] == "ultimo_estado_conocido"  # T-25: sin evidencia nueva no se afirma vigencia
    assert branches["B-2026"]["finality"] == "recurrida"  # T-23: firmeza separada
    assert card["evidence"] and card["evidence"][0]["url"].startswith("https://cortesuprema.gov.co/")
    timeline = q.call("public_case_timeline", {"p_case_id": case["case_id"]})
    same_day = [e for e in timeline if e["occurred_on"] == "2026-03-10"]
    assert len(same_day) == 2 and {e["proceeding_id"] for e in same_day} == {case["proc_a"], case["proc_b"]}  # T-27
    assert all(len(e["evidence"]) == 2 for e in same_day)  # T-26: una actuación, dos fuentes
    actors = q.call("public_case_actors", {"p_case_id": case["case_id"]})
    roles = {a["name"]: a["role"] for a in actors["participations"]}
    assert roles == {"Persona Sintética X": "investigado", "Testigo Sintético": "testigo"}  # T-22
    party = q.call("public_actor", {"p_actor_id": case["party"]})
    assert party["cases"] and all(c["relation"].startswith("por_afiliacion_de_") for c in party["cases"])  # T-21
    person = q.call("public_actor", {"p_actor_id": case["person"]})
    periods = {(p["entity"], p["starts_on"], p["ends_on"]) for p in person["positions"]}  # T-20
    assert ("Alcaldía de Florencia (sintética)", "2020-01-01", "2023-12-31") in periods and len(periods) == 2
    listing = q.call("public_cases_list", {"p_filters": {}, "p_limit": 10})
    assert listing["known_total"] == 1 and "no es un censo" in listing["coverage"]  # T-58


def test_stale_revision_cannot_overwrite(world, case) -> None:
    """T-32 / DB-03."""
    ed, rv = rpc(world, "editor"), rpc(world, "reviewer")
    rev2 = ed.call("editor_revise_case", {"p_case_id": case["case_id"], "p_expected_latest": 1, "p_title": "Caso sintético revisado",
                                          "p_summary": "Resumen sintético corregido para la segunda revisión.", "p_scope": "Pruebas",
                                          "p_coverage_note": "Datos sintéticos", "p_topics": ["contratacion"], "p_aliases": [],
                                          "p_territory_ids": [], "p_evidence_ids": [case["ev"]], "p_featured": False,
                                          "p_featured_reason": None})
    with pytest.raises(InsForgeError) as err:
        ed.call("editor_revise_case", {"p_case_id": case["case_id"], "p_expected_latest": 1, "p_title": "Otra edición concurrente",
                                       "p_summary": "Resumen de una edición basada en la revisión vieja.", "p_scope": "x",
                                       "p_coverage_note": "x", "p_topics": [], "p_aliases": [], "p_territory_ids": [],
                                       "p_evidence_ids": [case["ev"]], "p_featured": False, "p_featured_reason": None})
    assert err.value.sqlstate == "BC409"
    ed.call("editor_submit_case_revision", {"p_revision_id": rev2})
    with pytest.raises(InsForgeError):
        rv.call("reviewer_decide_case_revision", {"p_revision_id": rev2, "p_expected_latest": 1, "p_decision": "approve",
                                                  "p_reason": "versión vieja"})
    assert rv.call("reviewer_decide_case_revision", {"p_revision_id": rev2, "p_expected_latest": 2, "p_decision": "approve",
                                                     "p_reason": "segunda revisión"}) == "published"
    card = rpc(world, "query_service").call("public_case", {"p_case_id": case["case_id"]})
    assert card["title"] == "Caso sintético revisado" and card["revision_no"] == 2


def test_contradicting_evidence_is_kept(world, case) -> None:
    """T-29: ambas evidencias se conservan con su función."""
    ed = rpc(world, "editor")
    cid = ed.call("editor_create_claim", {"p_claim_type": "proceeding_status", "p_statement": "Estado sintético en disputa.",
                                          "p_subject_type": "proceeding", "p_subject_id": case["proc_b"], "p_object_type": None,
                                          "p_object_id": None, "p_value": {}, "p_sensitive": True, "p_evidence_ids": [case["ev"]]})
    world["db"].execute("insert into public.claim_evidence (claim_id, evidence_id, function) values (%s, %s, 'contradicts')",
                        (cid, case["ev2"]))
    rows = world["db"].execute("select function from public.claim_evidence where claim_id = %s order by function", (cid,))
    assert [r["function"] for r in rows] == ["contradicts", "supports"]


# -- seguimientos, outbox y correcciones -----------------------------------------------------------

def test_subscriptions_digest_and_corrections(world, case) -> None:
    """T-41..T-46, T-48, SUB-01..06."""
    admin, q = rpc(world, "admin"), rpc(world, "query_service")
    hash_a, hash_b = pseudonymize_user(KEY, BOT_ID, TG_A), pseudonymize_user(KEY, BOT_ID, TG_B)
    with pytest.raises(InsForgeError):  # bandera apagada: no hay seguimientos
        q.call("bot_subscribe", {"p_bot_id": BOT_ID, "p_user_hash": hash_a, "p_object_type": "case",
                                 "p_object_id": case["case_id"], "p_consent_version": "v1"})
    admin.call("admin_set_feature_flag", {"p_key": "FEATURE_SUBSCRIPTIONS", "p_enabled": True, "p_reason": "prueba de integración"})
    first = q.call("bot_subscribe", {"p_bot_id": BOT_ID, "p_user_hash": hash_a, "p_object_type": "case",
                                     "p_object_id": case["case_id"], "p_consent_version": "v1"})[0]
    again = q.call("bot_subscribe", {"p_bot_id": BOT_ID, "p_user_hash": hash_a, "p_object_type": "case",
                                     "p_object_id": case["case_id"], "p_consent_version": "v1"})[0]
    assert first["subscription_id"] == again["subscription_id"] and first["created"] and not again["created"]  # T-41
    sub_b = q.call("bot_subscribe", {"p_bot_id": BOT_ID, "p_user_hash": hash_b, "p_object_type": "case",
                                     "p_object_id": case["case_id"], "p_consent_version": "v1"})[0]
    assert q.call("bot_unsubscribe", {"p_bot_id": BOT_ID, "p_user_hash": hash_b,
                                      "p_subscription_id": first["subscription_id"]}) is False  # T-48
    assert len(q.call("bot_subscriptions", {"p_bot_id": BOT_ID, "p_user_hash": hash_a})) == 1

    new_claim = case["approved_claim"]("Nueva actuación sintética publicada.", t="event", st="proceeding", sid=case["proc_b"])
    events = world["db"].execute("select id from public.outbox_events where object_id = %s", (new_claim,))
    assert len(events) == 1  # T-42: un evento por publicación, no por seguidor
    q.call("digest_prepare", {})
    q.call("digest_prepare", {})  # idempotente
    per_user = world["db"].execute("select telegram_identity_id, count(*) n from public.notification_deliveries "
                                   "where outbox_event_id = %s group by 1", (events[0]["id"],))
    assert len(per_user) == 2 and all(r["n"] == 1 for r in per_user)

    q.call("bot_unsubscribe", {"p_bot_id": BOT_ID, "p_user_hash": hash_b, "p_subscription_id": sub_b["subscription_id"]})
    batch = {r["chat_id"]: r for r in q.call("digest_batch", {"p_include_digest": True, "p_limit": 50})}
    assert set(batch) == {TG_A}  # T-43: quien canceló antes del digest no recibe
    delivered = [i["delivery_id"] for i in batch[TG_A]["items"]]
    q.call("digest_mark", {"p_delivery_ids": delivered, "p_state": "sent", "p_message_id": 99, "p_error": None})

    rv = rpc(world, "reviewer")
    rv.call("reviewer_retract_claim", {"p_claim_id": new_claim, "p_reason": "identidad corregida tras revisión"})
    corrections = q.call("digest_batch", {"p_include_digest": False, "p_limit": 50})
    assert [r["chat_id"] for r in corrections] == [TG_A]  # T-45: quien recibió la versión errónea recibe la corrección
    assert corrections[0]["items"][0]["event_type"] == "correction"

    pending = case["approved_claim"]("Otra actuación sintética.", t="event", st="proceeding", sid=case["proc_b"])
    q.call("digest_prepare", {})
    rv.call("reviewer_retract_claim", {"p_claim_id": pending, "p_reason": "retirada antes del envío"})
    states = world["db"].execute("select d.state from public.notification_deliveries d join public.outbox_events e "
                                 "on e.id = d.outbox_event_id where e.object_id = %s and e.event_type = 'claim_published'", (pending,))
    assert states and all(s["state"] == "suppressed" for s in states)  # T-44

    item = q.call("digest_batch", {"p_include_digest": False, "p_limit": 50})[0]["items"][0]["delivery_id"]
    q.call("digest_mark", {"p_delivery_ids": [item], "p_state": "unknown_delivery", "p_message_id": None, "p_error": "TIMEOUT"})
    assert one(world, "select state from public.notification_deliveries where id = %s", item)["state"] == "unknown_delivery"  # T-46


def test_admin_command_idempotency(world) -> None:
    """T-55: misma clave y contenido ⇒ misma ejecución; otro contenido ⇒ conflicto."""
    admin = rpc(world, "admin")
    first = admin.call("admin_command_begin", {"p_type": "source.run", "p_key": "k-1", "p_payload_hash": "h1"})
    again = admin.call("admin_command_begin", {"p_type": "source.run", "p_key": "k-1", "p_payload_hash": "h1"})
    assert again["replayed"] and again["command_id"] == first["command_id"]
    with pytest.raises(InsForgeError) as err:
        admin.call("admin_command_begin", {"p_type": "source.run", "p_key": "k-1", "p_payload_hash": "otro"})
    assert err.value.sqlstate == "BC409"


def test_zero_paid_budget_and_commercial_providers_disabled(world) -> None:
    """T-51 / COS-01: presupuesto de pago 0 y proveedores comerciales apagados por defecto."""
    flags = rpc(world, "query_service").call("bot_feature_flags", {})
    assert flags["ENABLE_COMMERCIAL_PROVIDERS"] is False and flags["ENABLE_CPNU_AUTOMATION"] is False
    assert one(world, "select value from public.app_settings where key = 'paid_monthly_budget'")["value"] == 0


def test_telegram_views_render_against_real_sql(world, case) -> None:
    """Las vistas del bot leen las formas reales de las funciones públicas (sin campos inventados)."""
    from botcentro.telegram_ui.investigations import InvestigationViews

    admin = rpc(world, "admin")
    admin.call("admin_set_feature_flag", {"p_key": "FEATURE_CASES", "p_enabled": True, "p_reason": "prueba de vistas"})
    views = InvestigationViews(rpc(world, "query_service"), bot_id=BOT_ID)
    listing = views.cases({})
    assert listing.status == "ready" and "Caso sintético" in listing.blocks[0]
    card = views.case(case["case_id"])
    assert "Ramas (2)" in card.blocks[0] and card.evidence
    assert "Cronología" in views.case_timeline(case["case_id"]).blocks[0]
    assert "testigo" in views.case_actors(case["case_id"]).blocks[0]
    assert "Actores con rol" in views.proceeding(case["proc_b"]).blocks[0]
    assert "por afiliación de Persona Sintética X" in views.actor(case["party"]).blocks[0]
    homonyms = views.territory_resolve("San Pedro")
    assert homonyms.status == "partial" and len(homonyms.rows) >= 3
    florencia = views.territory(case["florencia"])
    assert "Florencia" in florencia.blocks[0] and "en validación" in florencia.blocks[0]  # contratos en sombra
    proceedings = views.proceedings({})
    assert proceedings.status == "ready" and "B-2026" in proceedings.blocks[0]  # A-2025 sin actores publicados
    assert "Procuraduría" in views.coverage().blocks[0]
    assert views.actor_search("Persona Sintética").view_type == "actor"  # una sola coincidencia publicada


def test_run_limits_end_partial_with_frozen_cutoff(world) -> None:
    """T-11/T-13 / ING-01/ING-04: el corte se fija en la primera página y agotar el límite termina en parcial."""
    db, ingest, fake = world["db"], world["users"]["ingest_service"], world["fake"]
    fake.rows["gdxc-w37w"] = DIVIPOLA
    fake.requests.clear()
    result = run_source(db, ingest, fake, "divipola", {}, world["tmp"], reparse=True, max_rows=2, page_size=1)
    assert result.status == "partial"
    cutoffs = {re.search(r":updated_at <= '([^']+)'", unquote_plus(u)).group(1)
               for u in fake.requests if "/resource/" in u}
    assert len(cutoffs) == 1


def test_failed_page_is_not_success(world) -> None:
    """T-09 / ING-04: una página que falla a mitad de la ejecución no se registra como éxito."""
    db, ingest, fake = world["db"], world["users"]["ingest_service"], world["fake"]
    original = fake.fetch
    calls = {"n": 0}

    def flaky(url, **kw):  # noqa: ANN001, ANN003, ANN202
        if "/resource/" in url:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise FetchError("HTTP_503", "servicio no disponible", kind=FailureKind.TRANSIENT)
        return original(url, **kw)

    fake.fetch = flaky
    try:
        result = run_source(db, ingest, fake, "divipola", {}, world["tmp"], reparse=True, page_size=1, max_attempts=1)
    finally:
        fake.fetch = original
    assert result.status != "succeeded"


def test_consortium_contractor_is_identified_by_supplier_code(world) -> None:
    """Consorcio sin NIT: identidad por código de proveedor SECOP; sin código, solo el nombre como texto."""
    db, ingest, fake = world["db"], world["users"]["ingest_service"], world["fake"]
    ut = {**contract_row("c-9", "CO1.SYN.9", "En ejecución", "500", "2026-03-01T00:00:00.000", "NIT", "No Definido"),
          "proveedor_adjudicado": "UNION TEMPORAL SINTETICA", "es_grupo": "Si", "codigo_proveedor": "734685258"}
    anon = {**contract_row("c-8", "CO1.SYN.8", "En ejecución", "700", "2026-03-02T00:00:00.000", "No Definido", "No Definido"),
            "proveedor_adjudicado": "CONSORCIO SIN CODIGO", "es_grupo": "Si"}
    fake.rows["jbjy-vk9h"] = [ut, anon]
    run_source(db, ingest, fake, "secop2_contracts",
               {"territories": [{"departamento": "Caquetá", "municipio": "Florencia"}], "from": "2026-01-01"}, world["tmp"])
    normalize(db, ingest, "contracts")
    rows = {r["native_id"]: r for r in db.execute(
        "select c.native_id, c.contractor_name_source, a.display_name, i.issuer, i.id_type, i.value_public "
        "from public.contracts c left join public.actors a on a.id = c.contractor_actor_id "
        "left join public.actor_identifiers i on i.actor_id = a.id where c.native_id in ('CO1.SYN.9', 'CO1.SYN.8')")}
    assert (rows["CO1.SYN.9"]["issuer"], rows["CO1.SYN.9"]["id_type"], rows["CO1.SYN.9"]["value_public"]) == \
        ("SECOP", "codigo_proveedor", "734685258")
    assert rows["CO1.SYN.8"]["display_name"] is None and rows["CO1.SYN.8"]["contractor_name_source"] == "CONSORCIO SIN CODIGO"


def test_full_object_entity_profile_and_state_versions(world) -> None:
    """Auditoría 2026-09-30: objeto completo sin versión nueva, misión/visión por código de entidad y ficha pública."""
    from botcentro.telegram_ui.investigations import InvestigationViews

    db, ingest, fake = world["db"], world["users"]["ingest_service"], world["fake"]
    scope = {"territories": [{"departamento": "Caquetá", "municipio": "Florencia"}], "from": "2026-01-01"}
    base = {**contract_row("c-7", "CO1.SYN.7", "En ejecución", "900", "2026-04-01T00:00:00.000"), "codigo_entidad": "704035211",
            "descripcion_del_proceso": "TEXTO CORTADO A TRESCIENTOS"}
    fake.rows["jbjy-vk9h"] = [base]
    run_source(db, ingest, fake, "secop2_contracts", scope, world["tmp"])
    normalize(db, ingest, "contracts")
    full = "OBJETO COMPLETO DEL CONTRATO " * 20
    fake.rows["jbjy-vk9h"] = [{**base, "objeto_del_contrato": full}]
    run_source(db, ingest, fake, "secop2_contracts", scope, world["tmp"])
    normalize(db, ingest, "contracts")
    row = one(world, "select c.id, c.object, (select count(*) from public.contract_versions v where v.contract_id = c.id) n "
                     "from public.contracts c where c.native_id = 'CO1.SYN.7'")
    assert row["n"] == 1 and row["object"].startswith("OBJETO COMPLETO")  # texto nuevo, misma versión de estado

    fake.rows["b6m4-qgqv"] = [
        {":id": "p-1", "identificador_unico": "CO1.APP.1", "anno": "2025", "codigo_entidad": "704035211",
         "nombre_entidad": "ALCALDIA SINTETICA", "mision_vision": "Mision vieja", "version": "3",
         "nombre_contacto": "FUNCIONARIO SINTETICO", "telefono_contacto": "3000000000", "correo_contacto": "x@y.co"},
        {":id": "p-2", "identificador_unico": "CO1.APP.2", "anno": "2026", "codigo_entidad": "704035211",
         "nombre_entidad": "ALCALDIA SINTETICA", "mision_vision": "Mision vigente sintetica",
         "perspectiva_estrategica": "Vision sintetica", "valor_presupuesto_general": "1000", "version": "5"}]
    run_source(db, ingest, fake, "secop2_paa", {"entity_codes": ["704035211"]}, world["tmp"])
    normalize(db, ingest, "entity_plans")
    dump = json.dumps(db.execute("select value_json from public.observations where predicate = 'entity_plan'"))
    assert "FUNCIONARIO SINTETICO" not in dump and "3000000000" not in dump  # sin datos de contacto
    db.execute("update public.sources set shadow_mode = false where code in ('SRC-15', 'SRC-25')")
    detail = rpc(world, "query_service").call("public_contract", {"p_contract_id": str(row["id"])})
    assert detail["entity_profile"]["year"] == 2026 and detail["entity_profile"]["mission_vision"] == "Mision vigente sintetica"
    view = InvestigationViews(rpc(world, "query_service"), bot_id=BOT_ID).contract(str(row["id"]))
    assert "OBJETO COMPLETO" in view.blocks[0] and "Vision sintetica" in view.blocks[0]
    db.execute("update public.sources set shadow_mode = true where code in ('SRC-15', 'SRC-25')")
    assert rpc(world, "query_service").call("public_contract", {"p_contract_id": str(row["id"])}) is None  # sombra: oculto



def test_resumed_run_continues_after_limit(world) -> None:
    """Regresión 2026-09-30: una ejecución reanudada no hereda el límite agotado de la anterior (ING-04)."""
    db, ingest, fake = world["db"], world["users"]["ingest_service"], world["fake"]
    fake.rows["gdxc-w37w"] = DIVIPOLA
    source = db.execute("select id from public.sources where code = 'SRC-20'")[0]["id"]
    first = run_source(db, ingest, fake, "divipola", {"resume": 1}, world["tmp"], reparse=True, max_rows=2, page_size=1)
    assert first.status == "partial"
    cursor = db.execute("select cursor_after from public.ingestion_runs where id = %s", (first.run_id,))[0]["cursor_after"]
    fake.requests.clear()
    second = run_source(db, ingest, fake, "divipola", {"resume": 1}, world["tmp"], reparse=True, previous=cursor,
                        max_rows=2, page_size=1)  # mismo límite que la primera: antes nacía agotada
    pages = [u for u in fake.requests if "/resource/" in u]
    assert len(pages) >= 2 and "%3Aid+%3E" in pages[0]  # reanudó por `:id > cursor` en vez de detenerse sin leer
    assert second.status == "partial"


def test_relatoria_keeps_every_topic_of_a_document(world) -> None:
    """La Relatoría publica una fila por tema: un documento, todos sus temas (antes quedaba solo el último)."""
    db, ingest, fake = world["db"], world["users"]["ingest_service"], world["fake"]
    url = {"url": "https://www.procuraduria.gov.co/sim/relatoria/.webdocumento?docId=1"}
    row = {"tipo_documento": "CONCEPTO (MISIONAL)", "n_mero_documento": "073-2025", "dependencia": "DELEGADA SINTETICA",
           "url_documento": url, "fecha_documento": "2025-06-03T00:00:00.000"}
    fake.rows["rhun-uf37"] = [{**row, ":id": "r-1", "tema": "PRINCIPIO PRO ACTIONE", "subtema": "Aplica"},
                              {**row, ":id": "r-2", "tema": "REPARACION DIRECTA", "subtema": "Caducó"}]
    run_source(db, ingest, fake, "relatoria_pgn", {"from": "2025-01-01"}, world["tmp"])
    normalize(db, ingest, "documents")
    docs = db.execute("select topics from public.official_documents where number = '073-2025'")
    assert len(docs) == 1
    assert {t["tema"] for t in docs[0]["topics"]} == {"PRINCIPIO PRO ACTIONE", "REPARACION DIRECTA"}
