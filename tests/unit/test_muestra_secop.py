"""Muestra de verificación de SECOP (§24.6): selección estratificada reproducible y comparación con la fuente."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from botcentro.investigations.muestra import SampleItem, choose, compare, stored_view, write_report
from botcentro.investigations.results import AdapterResult, Status

KEY = b"k" * 32


def row(n: int, territory: str, status: str = "En ejecución", value: int = 100, ctype: str = "Prestación de servicios",
        person: bool = True) -> dict:
    return {"native_id": f"CO1.PCCNTR.{n}", "territory": territory, "status_original": status,
            "value_initial": Decimal(value), "value_current": Decimal(value), "signed_on": date(2026, 2, 1),
            "contract_type": ctype, "modality": "Contratación directa", "object": "objeto", "official_url": f"https://community.secop.gov.co/{n}",
            "entity": "ALCALDIA", "entity_nit": "0891180009", "contractor": "PERSONA X" if person else "EMPRESA",
            "contractor_type": "persona" if person else "organizacion_privada",
            "contractor_key": "h" * 64 if person else "0800123456", "contractor_masked": "*****345", "versions": 1}


def test_sample_is_stratified_and_reproducible() -> None:
    rows = [row(i, "FLORENCIA", value=i) for i in range(40)] + [row(100 + i, "ARAUCA", value=i) for i in range(40)]
    rows[5]["status_original"] = "Modificado"
    rows[6]["status_original"] = "cedido"
    rows[7]["contract_type"] = "Obra"
    a, b = choose(rows, per_territory=7), choose(rows, per_territory=7)
    assert a == b and len(a) == 14
    reasons = {reason for reason, r in a if r["territory"] == "FLORENCIA"}
    assert {"mayor valor", "estado Modificado", "estado poco frecuente", "tipo distinto de prestación de servicios"} <= reasons
    assert next(r for reason, r in a if reason == "mayor valor" and r["territory"] == "FLORENCIA")["value_current"] == 39
    assert len({r["native_id"] for _, r in a}) == 14


def test_compare_flags_mapping_differences_and_missing_rows() -> None:
    r = row(1, "FLORENCIA", person=False)
    same = {"id_contrato": "CO1.PCCNTR.1", "estado_contrato": "En ejecución", "valor_del_contrato": "100",
            "fecha_de_firma": "2026-02-01T00:00:00.000", "nit_entidad": "0891180009", "tipodocproveedor": "NIT",
            "documento_proveedor": "0800123456", "proveedor_adjudicado": "EMPRESA", "tipo_de_contrato": "Prestación de servicios",
            "urlproceso": {"url": "https://community.secop.gov.co/1"}}
    responses = iter([[same], [{**same, "nit_entidad": "891180009"}], []])
    client = SimpleNamespace(fetch_page=lambda *a, **k: AdapterResult("SRC-15", Status.OK, items=next(responses)))
    items = [SampleItem("x", "CO1.PCCNTR.1", "FLORENCIA", stored_view(r)) for _ in range(3)]
    compare(items, client, KEY)  # type: ignore[arg-type]
    assert items[0].diffs == [] and items[1].diffs == ["nit_entidad"] and items[2].fetch_error == "ya no aparece en la fuente"


def test_report_lists_checklist_without_plain_documents(tmp_path) -> None:
    item = SampleItem("mayor valor", "CO1.PCCNTR.1", "FLORENCIA", stored_view(row(1, "FLORENCIA")), source={}, diffs=[])
    md, csv_path = write_report([item], tmp_path, today=date(2026, 9, 30))
    text = md.read_text()
    assert "- [ ] Coincide en el portal" in text and "doc. *****345" in text and "h" * 64 not in text
    assert csv_path.read_text().count("\n") == 2
