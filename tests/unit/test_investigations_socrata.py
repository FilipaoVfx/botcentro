"""Capa Socrata y valores de investigaciones, con metadatos reales versionados (investigaciones §21)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from botcentro.investigations.datasets import SPECS, parse_secop2_contract, parse_siri
from botcentro.investigations.socrata import Condition, Query, check_schema, schema_fingerprint, soql_literal
from botcentro.investigations.values import (InvalidValue, identifier, identity_hmac, is_person_document, mask, money,
                                             source_date)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "investigaciones"
KEY = b"k" * 32


def meta(dataset: str) -> dict:
    return json.loads((FIXTURES / f"meta-{dataset}.json").read_text())


@pytest.mark.parametrize("adapter", sorted(SPECS))
def test_mappings_only_cite_published_fields(adapter: str) -> None:
    """T-08: cada campo crítico del mapeo existe en los metadatos reales de la fuente."""
    spec = SPECS[adapter]
    check = check_schema(meta(spec.dataset_id), spec.critical, spec.optional)
    assert check.missing_critical == [], check.missing_critical


def test_schema_change_is_detected() -> None:
    """T-06/T-07: quitar un campo crítico ⇒ incompatible; la huella cambia con el esquema."""
    spec = SPECS["secop2_contracts"]
    m = meta(spec.dataset_id)
    before = check_schema(m, spec.critical, spec.optional)
    m["columns"] = [c for c in m["columns"] if c["fieldName"] != spec.critical[0]]
    after = check_schema(m, spec.critical, spec.optional)
    assert after.result == "incompatible" and after.fingerprint != before.fingerprint
    assert schema_fingerprint({"a": "text", "b": "number"}) == schema_fingerprint({"b": "number", "a": "text"})


def test_soql_escaping_and_whitelist() -> None:
    """T-01/T-02: apóstrofes duplicados; control y flotantes rechazados; campos fuera del mapeo rechazados."""
    assert soql_literal("D'ANGELO") == "'D''ANGELO'"
    assert soql_literal(Decimal("-12.50")) == "-12.50"
    for bad in ("a\nb", 1.5, None):
        with pytest.raises(ValueError):
            soql_literal(bad)
    q = Query(frozenset({"ciudad"}), [Condition("ciudad", "=", "x' OR 1=1 --")])
    assert q.params()["$where"] == "ciudad = 'x'' OR 1=1 --'"
    with pytest.raises(ValueError):
        Query(frozenset({"ciudad"}), [Condition("otro_campo", "=", "x")]).params()
    with pytest.raises(ValueError):
        Query(frozenset({"ciudad"}), [Condition("ciudad", "; drop", "x")]).params()
    assert Query(frozenset({"ciudad"}), [Condition("ciudad", "in", [])]).params()["$where"] == "false"


def test_scope_keeps_unsigned_contracts() -> None:
    """T-17: el alcance por fecha de firma incluye contratos sin firma (nulos) y se declara explícito."""
    spec = SPECS["secop2_contracts"]
    conditions, groups = spec.scope_conditions({"from": "2026-01-01",
                                                "territories": [{"departamento": "Caquetá", "municipio": "Florencia"}]})
    assert any(c.op == "is null" for g in groups for c in g)
    assert any(c.op == "in" and "Florencia" in c.value for c in conditions)


def test_money_identifier_and_dates() -> None:
    """T-03/T-04/T-05/T-18: signo y decimales exactos; ceros iniciales; fechas sin hora inventada."""
    assert money("-12.50") == Decimal("-12.50") and money("") is None and money("1e3") == Decimal("1E+3")
    with pytest.raises(InvalidValue):
        money(12.5)
    with pytest.raises(InvalidValue):
        money("doce")
    assert identifier("0800123456   ") == "0800123456" and identifier("NA") is None
    with pytest.raises(InvalidValue):
        identifier(800123456.0)
    assert source_date("15/03/2025") == date(2025, 3, 15) and source_date("2026-02-01T00:00:00.000") == date(2026, 2, 1)


def test_person_documents_are_pseudonymised() -> None:
    """T-11/T-12/SEG-02: documento de persona ⇒ HMAC estable y enmascarado; NIT se conserva público."""
    assert mask("10000001") == "*****001"
    assert identity_hmac(KEY, "RNEC", "CC", "10.000.001") == identity_hmac(KEY, "RNEC", "CC", "10000001")
    assert identity_hmac(KEY, "RNEC", "CC", "10000001") != identity_hmac(b"x" * 32, "RNEC", "CC", "10000001")
    assert is_person_document("Cédula de Ciudadanía") and not is_person_document("NIT")
    row = {"numero_siri": "900001", "primer_nombre": "JUAN", "primer_apellido": "PEREZ", "segundo_apellido": "NA",
           "nombre_tipo_identificacion": "CÉDULA DE CIUDADANÍA", "numero_identificacion": "10000001  ",
           "fecha_efectos_juridicos": "15/03/2025", "sanciones": "MULTA"}
    cand = parse_siri(row, KEY)
    dumped = json.dumps(cand.value)
    assert "10000001" not in dumped and cand.value["person"]["name"] == "JUAN PEREZ"
    assert cand.value["person"]["id_masked"].endswith("001")


def test_contract_parser_keeps_nit_and_sign() -> None:
    row = {"id_contrato": "CO1.SYN.1", "nit_entidad": "0891180009", "nombre_entidad": "ALCALDIA",
           "tipodocproveedor": "NIT", "documento_proveedor": "0800123456", "proveedor_adjudicado": "P SAS",
           "valor_del_contrato": "-12.50", "estado_contrato": "En ejecución", "departamento": "Caquetá",
           "ciudad": "Florencia"}
    cand = parse_secop2_contract(row, KEY)
    assert cand.value["entity"]["nit"] == "0891180009" and cand.value["value_initial"] == "-12.50"
    assert cand.value["contractor"]["id_public"] == "0800123456"


def test_assisted_upload_keeps_no_pdf_and_reports_quarantine() -> None:
    """DEC-11 / T-28: sin PDF guardado; páginas dudosas se informan en cuarentena; solo PDF."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from botcentro.documents.pdf_text import PageResult
    from botcentro.domain.enums import QualityStatus
    from botcentro.http.fetcher import Fetched
    from botcentro.investigations.assisted import AssistedError, AssistedRequest, register_document

    calls = []
    rpc = SimpleNamespace(call=lambda fn, params: calls.append((fn, params)) or {"revision_id": "r1", "new_version": True})

    def fetcher(mime: str):  # noqa: ANN202
        return SimpleNamespace(fetch=lambda url: Fetched(url, url, 200, b"%PDF-1.7", "h" * 64, mime, mime, None, None,
                                                         datetime.now(timezone.utc)))

    req = AssistedRequest("SRC-22", "https://cortesuprema.gov.co/b.pdf", "Boletín sintético", "boletin")
    pages = [PageResult(1, "texto", "native", QualityStatus.ACCEPTED), PageResult(2, "", "ocr", QualityStatus.REVIEW_REQUIRED)]
    out = register_document(rpc, fetcher("application/pdf"), req, extract=lambda b: pages)  # type: ignore[arg-type]
    assert out["quarantined_pages"] == [2] and calls[0][1]["p_object_key"] is None
    with pytest.raises(AssistedError):
        register_document(rpc, fetcher("text/html"), req, extract=lambda b: pages)  # type: ignore[arg-type]
    with pytest.raises(AssistedError):
        register_document(rpc, fetcher("application/pdf"),  # type: ignore[arg-type]
                          AssistedRequest("SRC-22", "http://cortesuprema.gov.co/b.pdf", "Boletín", "boletin"))


def test_combined_status_never_hides_failures() -> None:
    """T-01/T-02/T-03: todo falla ⇒ unavailable; una falla ⇒ partial con la fuente; vacío válido ⇒ empty."""
    from botcentro.investigations.results import AdapterResult, Status, combine

    down = AdapterResult(source_id="SRC-15", status=Status.UNAVAILABLE)
    empty = AdapterResult(source_id="SRC-17", status=Status.EMPTY)
    full = AdapterResult(source_id="SRC-18", status=Status.OK, items=[{"x": 1}])
    assert combine([down, AdapterResult(source_id="SRC-16", status=Status.UNAVAILABLE)])["status"] == "unavailable"
    mixed = combine([down, full])
    assert mixed["status"] == "partial" and mixed["failed_sources"] == ["SRC-15"]
    assert combine([empty])["status"] == "empty"


def test_tls_verification_is_always_on() -> None:
    """T-54 / ING-06: el transporte verifica certificado y nombre; no hay forma de desactivarlo."""
    import ssl

    from botcentro.http.fetcher import GuardedTransport
    from botcentro.security.url_guard import UrlGuard, UrlPolicy

    transport = GuardedTransport(UrlGuard(UrlPolicy.for_domains(["www.datos.gov.co"])))
    context = transport._pool._ssl_context  # noqa: SLF001
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_consortium_uses_secop_supplier_code_never_name() -> None:
    """Auditoría 2026-09-30: consorcios con documento «No Definido» se identifican por código de proveedor."""
    base = {"id_contrato": "CO1.PCCNTR.9", "nit_entidad": "800095728", "nombre_entidad": "MUNICIPIO",
            "tipodocproveedor": "NIT", "documento_proveedor": "No Definido",
            "proveedor_adjudicado": "UNIÓN TEMPORAL SINTÉTICA 2026", "valor_del_contrato": "1"}
    group = parse_secop2_contract({**base, "es_grupo": "Si", "codigo_proveedor": "734685258"}, KEY).value["contractor"]
    assert group["kind"] == "organizacion" and group["issuer"] == "SECOP" and group["id_type"] == "codigo_proveedor"
    assert group["id_public"] == "734685258"
    unknown = parse_secop2_contract({**base, "es_grupo": "No", "codigo_proveedor": "734685258"}, KEY).value["contractor"]
    assert unknown["kind"] == "desconocido" and "id_public" not in unknown and unknown["name"]


def test_nit_of_natural_person_is_protected_and_unified() -> None:
    """Privacidad (2026-09-30): el NIT de una persona natural no queda en claro y se une con su cédula."""
    base = {"id_contrato": "X", "nit_entidad": "1", "nombre_entidad": "E", "valor_del_contrato": "1", "tipodocproveedor": "NIT"}
    company = parse_secop2_contract({**base, "documento_proveedor": "900123456", "proveedor_adjudicado": "Acme S.A.S."}, KEY)
    assert company.value["contractor"]["kind"] == "organizacion" and company.value["contractor"]["id_public"] == "900123456"
    by_nit = parse_secop2_contract({**base, "documento_proveedor": "801234561", "proveedor_adjudicado": "Juan Pérez",
                                    "identificaci_n_representante_legal": "80123456"}, KEY).value["contractor"]
    by_cc = parse_secop2_contract({**base, "tipodocproveedor": "Cédula de Ciudadanía", "documento_proveedor": "80123456",
                                   "proveedor_adjudicado": "Juan Pérez"}, KEY).value["contractor"]
    assert by_nit["kind"] == "persona" and "id_public" not in by_nit and by_nit["id_hmac"] == by_cc["id_hmac"]
    unknown = parse_secop2_contract({**base, "documento_proveedor": "12345678", "proveedor_adjudicado": "Nombre Sin Marca",
                                     "nombre_representante_legal": "Sin Descripcion"}, KEY).value["contractor"]
    assert unknown["kind"] == "sin_clasificar" and "id_public" not in unknown and "12345678" not in str(unknown)
