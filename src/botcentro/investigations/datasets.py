"""Conjuntos de datos.gov.co del módulo de investigaciones: mapeo versionado y parseo (SRC-05, §11.1).

Cada especificación declara campos críticos (su ausencia detiene la carga) y opcionales (su
ausencia degrada y se muestra). Los parsers conservan el valor original junto al normalizado y
nunca guardan documentos de personas: solo HMAC y valor enmascarado (SEG-02, DAT-04).
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from botcentro.connectors.base import NormalizedCandidate
from botcentro.domain.dates import PartialDate
from botcentro.investigations.socrata import Condition
from botcentro.investigations.values import (identifier, identity_hmac, is_person_document, mask, money,
                                             source_date)

MAPPING_VERSION = "mapeo-2026-09-30"
Parser = Callable[[Mapping[str, Any], bytes], NormalizedCandidate]


def fold(text: str | None) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper().strip()


def name_variants(name: str) -> list[str]:
    """Las fuentes escriben el mismo municipio con distinta capitalización o sin tildes."""
    return sorted({name, name.upper(), fold(name), name.title()})


def _day(value: object) -> PartialDate:
    d = source_date(value)
    return PartialDate.of_day(d) if d else PartialDate.unknown()


def _money_text(value: object) -> str | None:
    amount = money(value)
    return None if amount is None else str(amount)


def _party(key: bytes, issuer: str, id_type: str | None, raw_id: object, name: str | None) -> dict[str, Any]:
    """Contratista, proveedor o persona: NIT público; documento de persona natural solo protegido."""
    value = identifier(raw_id)
    party: dict[str, Any] = {"name": (name or "").strip() or None, "id_type": (id_type or "").strip() or None}
    if value is None:
        party["kind"] = "desconocido"
        return party
    if is_person_document(id_type):
        party.update(kind="persona", id_hmac=identity_hmac(key, issuer, party["id_type"] or "doc", value),
                     id_masked=mask(value))
    else:
        party.update(kind="organizacion", id_public=value, id_masked=mask(value, visible=4))
    return party


def _contractor(key: bytes, row: Mapping[str, Any]) -> dict[str, Any]:
    """Contratista de SECOP II. Consorcios y uniones temporales (`es_grupo = Si`) se publican sin NIT
    («No Definido»); su identidad es el código de proveedor de SECOP, estable y no personal. Sin
    documento ni código no hay identidad: el nombre se conserva solo como texto (DAT-05)."""
    party = _party(key, "SECOP", row.get("tipodocproveedor"), row.get("documento_proveedor"), row.get("proveedor_adjudicado"))
    code = identifier(row.get("codigo_proveedor"))
    if party["kind"] == "desconocido" and code and str(row.get("es_grupo") or "").strip().lower() == "si":
        party.update(kind="organizacion", issuer="SECOP", id_type="codigo_proveedor", id_public=code,
                     id_masked=mask(code, visible=4), group=True)
    return party


def _clip(text: object, limit: int) -> str | None:
    value = " ".join(str(text or "").split())
    return value[:limit] or None


@dataclass(frozen=True)
class DatasetSpec:
    code: str
    dataset_id: str
    adapter: str
    name: str
    institution: str
    critical: Sequence[str]
    optional: Sequence[str]
    parser: Parser
    location_fields: tuple[str, str] | None = None  # (departamento, municipio)
    date_field: str | None = None
    include_null_dates: bool = False
    updated_field: str | None = None
    capabilities: Mapping[str, str] = field(default_factory=dict)
    entity_code_field: str | None = None  # alcance por códigos de entidad SECOP (scope["entity_codes"])

    @property
    def fields(self) -> frozenset[str]:
        return frozenset([*self.critical, *self.optional])

    def scope_conditions(self, scope: Mapping[str, Any]) -> tuple[list[Condition], list[list[Condition]]]:
        """Filtros declarados como alcance (§11.1): se muestran siempre, nunca se heredan en silencio."""
        conditions: list[Condition] = []
        groups: list[list[Condition]] = []
        territories = scope.get("territories") or []
        if territories and self.location_fields:
            dept, muni = self.location_fields
            conditions.append(Condition(muni, "in", sorted({v for t in territories for v in name_variants(t["municipio"])})))
            conditions.append(Condition(dept, "in", sorted({v for t in territories for v in name_variants(t["departamento"])})))
        if self.entity_code_field and scope.get("entity_codes"):
            conditions.append(Condition(self.entity_code_field, "in", sorted({str(c) for c in scope["entity_codes"]})))
        if self.date_field and scope.get("from"):
            span = [Condition(self.date_field, ">=", scope["from"])]
            if scope.get("to"):
                span.append(Condition(self.date_field, "<=", scope["to"] + "T23:59:59"))
            if self.include_null_dates:
                # Contratos sin fecha de firma: representables si existen (T-17). Se pide el rango o nulo.
                groups.append([span[0], Condition(self.date_field, "is null")])
                conditions.extend(span[1:])
            else:
                conditions.extend(span)
        if self.updated_field and scope.get("updated_since"):
            conditions.append(Condition(self.updated_field, ">=", scope["updated_since"]))
        return conditions, groups


# -- parsers ---------------------------------------------------------------------------------------

def parse_secop2_contract(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    native = identifier(row.get("id_contrato"))
    if not native:
        raise ValueError("contrato sin id_contrato")
    value = {
        "native_id": native, "dataset": "jbjy-vk9h",
        "entity": {"name": _clip(row.get("nombre_entidad"), 300), "nit": identifier(row.get("nit_entidad")),
                   "code": identifier(row.get("codigo_entidad")),
                   "departamento": row.get("departamento"), "municipio": row.get("ciudad"),
                   "orden": row.get("orden"), "sector": row.get("sector")},
        "contractor": _contractor(key, row),
        "status": row.get("estado_contrato"), "signed_on": _iso(row.get("fecha_de_firma")),
        "starts_on": _iso(row.get("fecha_de_inicio_del_contrato")), "ends_on": _iso(row.get("fecha_de_fin_del_contrato")),
        "value_initial": _money_text(row.get("valor_del_contrato")), "value_paid": _money_text(row.get("valor_pagado")),
        "value_invoiced": _money_text(row.get("valor_facturado")), "days_added": identifier(row.get("dias_adicionados")),
        "modality": row.get("modalidad_de_contratacion"), "contract_type": row.get("tipo_de_contrato"),
        # `descripcion_del_proceso` llega cortada a 300 caracteres en la fuente; el concepto completo es
        # `objeto_del_contrato` (auditoría 2026-09-30).
        "object": _clip(row.get("objeto_del_contrato") or row.get("descripcion_del_proceso"), 4000),
        "reference": row.get("referencia_del_contrato"),
        "process_id": row.get("proceso_de_compra"), "url": (row.get("urlproceso") or {}).get("url"),
        "updated_on": _iso(row.get("ultima_actualizacion")), "currency": "COP",
    }
    return NormalizedCandidate("contract", f"secop2:contrato:{native}", "contract_snapshot", value,
                               effective=_day(row.get("fecha_de_firma")), record_pointer=native)


def parse_secop2_process(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    native = identifier(row.get("id_del_proceso"))
    if not native:
        raise ValueError("proceso sin id_del_proceso")
    value = {
        "native_id": native, "dataset": "p6dx-8zbt",
        "entity": {"name": _clip(row.get("entidad"), 300), "nit": identifier(row.get("nit_entidad")),
                   "departamento": row.get("departamento_entidad"), "municipio": row.get("ciudad_entidad")},
        "phase": row.get("fase"), "status": row.get("estado_del_procedimiento"), "awarded": row.get("adjudicado"),
        "published_on": _iso(row.get("fecha_de_publicacion_del")), "awarded_on": _iso(row.get("fecha_adjudicacion")),
        "base_price": _money_text(row.get("precio_base")), "awarded_value": _money_text(row.get("valor_total_adjudicacion")),
        "modality": row.get("modalidad_de_contratacion"), "name": _clip(row.get("nombre_del_procedimiento"), 600),
        "supplier": _party(key, "SECOP", "NIT" if row.get("nit_del_proveedor_adjudicado") else None,
                           row.get("nit_del_proveedor_adjudicado"), row.get("nombre_del_proveedor")),
        "url": (row.get("urlproceso") or {}).get("url"),
    }
    return NormalizedCandidate("contract_process", f"secop2:proceso:{native}", "process_snapshot", value,
                               effective=_day(row.get("fecha_de_publicacion_del")), record_pointer=native)


def parse_secop1(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    native = identifier(row.get("uid"))
    if not native:
        raise ValueError("registro de SECOP I sin uid")
    value = {
        "native_id": native, "dataset": "f789-7hwg",
        "entity": {"name": _clip(row.get("nombre_entidad"), 300), "nit": identifier(row.get("nit_de_la_entidad")),
                   "departamento": row.get("departamento_entidad"), "municipio": row.get("municipio_entidad")},
        "contractor": _party(key, "SECOP", row.get("tipo_identifi_del_contratista"),
                             row.get("identificacion_del_contratista"), row.get("nom_razon_social_contratista")),
        "status": row.get("estado_del_proceso"), "signed_on": _iso(row.get("fecha_de_firma_del_contrato")),
        "starts_on": _iso(row.get("fecha_ini_ejec_contrato")), "ends_on": _iso(row.get("fecha_fin_ejec_contrato")),
        "value_initial": _money_text(row.get("cuantia_contrato")),
        "value_additions": _money_text(row.get("valor_total_de_adiciones")),
        "value_current": _money_text(row.get("valor_contrato_con_adiciones")),
        "modality": row.get("modalidad_de_contratacion"), "contract_type": row.get("tipo_de_contrato"),
        "object": _clip(row.get("detalle_del_objeto_a_contratar"), 1200), "reference": row.get("numero_de_contrato"),
        "process_id": row.get("numero_de_proceso"), "updated_on": _iso(row.get("ultima_actualizacion")),
        "currency": "COP",
    }
    return NormalizedCandidate("contract", f"secop1:contrato:{native}", "contract_snapshot", value,
                               effective=_day(row.get("fecha_de_firma_del_contrato")), record_pointer=native)


def parse_siri(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    native = identifier(row.get("numero_siri"))
    if not native:
        raise ValueError("registro SIRI sin numero_siri")
    names = " ".join(str(x).strip() for x in (row.get("primer_nombre"), row.get("segundo_nombre"),
                                              row.get("primer_apellido"), row.get("segundo_apellido"))
                     if x and str(x).strip().upper() not in {"NA", "N/A", "NO APLICA", "-", "."})
    person = _party(key, "RNEC", row.get("nombre_tipo_identificacion") or row.get("tipo_identificacion"),
                    row.get("numero_identificacion"), " ".join(names.split()))
    duration = {k: identifier(row.get(f"duracion_{k}")) for k in ("anos", "mes", "dias")}
    value = {
        "native_id": native, "dataset": "iaeu-rcn6", "record_type": row.get("tipo_inhabilidad"),
        "person": person, "person_quality": row.get("calidad_persona"), "position": row.get("cargo"),
        "place": {"departamento": row.get("lugar_hechos_departamento"), "municipio": row.get("lugar_hechos_municipio")},
        "sanction": row.get("sanciones"), "duration": {k: v for k, v in duration.items() if v},
        "instance": row.get("providencia"), "authority": row.get("autoridad"),
        "effects_on": _iso(row.get("fecha_efectos_juridicos")), "proceeding_number": identifier(row.get("numero_proceso")),
        "entity": {"name": row.get("entidad_sancionado"), "departamento": row.get("entidad_departamento"),
                   "municipio": row.get("entidad_municipio")},
    }
    return NormalizedCandidate("sanction_record", f"siri:{native}", "siri_record", value,
                               effective=_day(row.get("fecha_efectos_juridicos")), record_pointer=native)


def parse_relatoria(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    url = (row.get("url_documento") or {}).get("url") if isinstance(row.get("url_documento"), Mapping) else row.get("url_documento")
    basis = url or f"{row.get('tipo_documento')}|{row.get('n_mero_documento')}|{row.get('fecha_documento')}"
    native = hashlib.sha1(str(basis).encode()).hexdigest()[:20]
    value = {"native_id": native, "dataset": "rhun-uf37", "doc_type": row.get("tipo_documento"),
             "number": identifier(row.get("n_mero_documento")), "dependency": row.get("dependencia"),
             "topic": _clip(row.get("tema"), 300), "subtopic": _clip(row.get("subtema"), 300), "url": url,
             "doc_date": _iso(row.get("fecha_documento"))}
    return NormalizedCandidate("official_document", f"pgn-relatoria:{native}", "official_document", value,
                               effective=_day(row.get("fecha_documento")), record_pointer=native)


def parse_divipola(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    code = identifier(row.get("cod_mpio"))
    if not code or not code.isdigit() or len(code) != 5:
        raise ValueError(f"código DIVIPOLA inválido: {code!r}")
    value = {"code": code, "name": row.get("nom_mpio"), "department_code": identifier(row.get("cod_dpto")),
             "department": row.get("dpto"), "kind": row.get("tipo_municipio")}
    return NormalizedCandidate("territory", f"divipola:{code}", "territory", value, record_pointer=code)


def parse_paa_header(row: Mapping[str, Any], key: bytes) -> NormalizedCandidate:
    """Encabezado del Plan Anual de Adquisiciones: misión, visión (perspectiva estratégica) y presupuesto
    de la entidad, por año. Los datos de contacto (nombre, teléfono y correo de funcionarios) no se
    guardan: no hacen falta para el producto (minimización de datos personales)."""
    native = identifier(row.get("identificador_unico"))
    code = identifier(row.get("codigo_entidad"))
    year = identifier(row.get("anno"))
    if not native or not code or not year or not year.isdigit():
        raise ValueError("encabezado de PAA sin identificador, entidad o año")
    value = {
        "native_id": native, "dataset": "b6m4-qgqv", "entity_code": code, "entity_name": _clip(row.get("nombre_entidad"), 300),
        "year": int(year), "mission_vision": _clip(row.get("mision_vision"), 6000),
        "strategic_perspective": _clip(row.get("perspectiva_estrategica"), 6000),
        "general_budget": _money_text(row.get("valor_presupuesto_general")), "plan_version": identifier(row.get("version")),
        "published_on": _iso(str(row.get("fecha_de_publicacion") or "")[:10] or None),
        "modified_on": _iso(str(row.get("fecha_de_ultima_modificacion") or "")[:10] or None),
        "state": row.get("estado"), "departamento": row.get("departamento_paa"), "municipio": row.get("municipio_paa"),
    }
    return NormalizedCandidate("entity_plan", f"secop2:paa:{native}", "entity_plan", value, record_pointer=native)


def _iso(value: object) -> str | None:
    d = source_date(value)
    return d.isoformat() if d else None


SPECS: dict[str, DatasetSpec] = {
    "secop2_paa": DatasetSpec(
        "SRC-25", "b6m4-qgqv", "secop2_paa", "SECOP II · Plan Anual de Adquisiciones (encabezado)", "Colombia Compra Eficiente",
        critical=("identificador_unico", "anno", "codigo_entidad", "nombre_entidad", "mision_vision"),
        optional=("perspectiva_estrategica", "valor_presupuesto_general", "fecha_de_publicacion",
                  "fecha_de_ultima_modificacion", "version", "estado", "departamento_paa", "municipio_paa"),
        parser=parse_paa_header, entity_code_field="codigo_entidad",
        capabilities={"discovery": "yes", "detail": "yes", "identifiers": "yes", "history": "yes"}),
    "secop2_contracts": DatasetSpec(
        "SRC-15", "jbjy-vk9h", "secop2_contracts", "SECOP II · Contratos electrónicos", "Colombia Compra Eficiente",
        critical=("id_contrato", "nombre_entidad", "nit_entidad", "departamento", "ciudad", "estado_contrato",
                  "valor_del_contrato", "proveedor_adjudicado", "documento_proveedor", "tipodocproveedor", "urlproceso",
                  "fecha_de_firma"),
        optional=("descripcion_del_proceso", "fecha_de_inicio_del_contrato", "fecha_de_fin_del_contrato", "valor_pagado",
                  "valor_facturado", "dias_adicionados", "modalidad_de_contratacion", "tipo_de_contrato",
                  "referencia_del_contrato", "proceso_de_compra", "ultima_actualizacion", "orden", "sector",
                  "es_grupo", "codigo_proveedor", "objeto_del_contrato", "codigo_entidad"),
        parser=parse_secop2_contract, location_fields=("departamento", "ciudad"), date_field="fecha_de_firma",
        include_null_dates=True, updated_field="ultima_actualizacion",
        capabilities={"discovery": "yes", "detail": "yes", "incremental": "yes", "history": "partial",
                      "identifiers": "yes", "deletions": "unsupported", "totals": "yes", "documents": "unsupported"}),
    "secop2_processes": DatasetSpec(
        "SRC-16", "p6dx-8zbt", "secop2_processes", "SECOP II · Procesos de contratación", "Colombia Compra Eficiente",
        critical=("id_del_proceso", "entidad", "nit_entidad", "departamento_entidad", "ciudad_entidad", "fase",
                  "estado_del_procedimiento", "fecha_de_publicacion_del", "precio_base", "adjudicado"),
        optional=("valor_total_adjudicacion", "fecha_adjudicacion", "nombre_del_proveedor", "nit_del_proveedor_adjudicado",
                  "modalidad_de_contratacion", "nombre_del_procedimiento", "urlproceso"),
        parser=parse_secop2_process, location_fields=("departamento_entidad", "ciudad_entidad"),
        date_field="fecha_de_publicacion_del",
        capabilities={"discovery": "yes", "detail": "yes", "incremental": "unsupported", "history": "partial",
                      "identifiers": "yes", "deletions": "unsupported", "totals": "yes", "documents": "unsupported"}),
    "secop1": DatasetSpec(
        "SRC-17", "f789-7hwg", "secop1", "SECOP I · Procesos de compra pública", "Colombia Compra Eficiente",
        critical=("uid", "nombre_entidad", "nit_de_la_entidad", "estado_del_proceso", "municipio_entidad",
                  "departamento_entidad", "cuantia_contrato"),
        optional=("tipo_identifi_del_contratista", "identificacion_del_contratista", "nom_razon_social_contratista",
                  "fecha_de_firma_del_contrato", "fecha_ini_ejec_contrato", "fecha_fin_ejec_contrato",
                  "valor_total_de_adiciones", "valor_contrato_con_adiciones", "modalidad_de_contratacion",
                  "tipo_de_contrato", "detalle_del_objeto_a_contratar", "numero_de_contrato", "numero_de_proceso",
                  "ultima_actualizacion"),
        parser=parse_secop1, location_fields=("departamento_entidad", "municipio_entidad"),
        date_field="fecha_de_firma_del_contrato", include_null_dates=True, updated_field="ultima_actualizacion",
        capabilities={"discovery": "yes", "detail": "yes", "incremental": "yes", "history": "yes",
                      "identifiers": "yes", "deletions": "unsupported", "totals": "yes", "documents": "unsupported"}),
    "siri": DatasetSpec(
        "SRC-18", "iaeu-rcn6", "siri", "SIRI · Antecedentes (sanciones e inhabilidades)", "Procuraduría General de la Nación",
        critical=("numero_siri", "tipo_inhabilidad", "numero_identificacion", "primer_apellido", "primer_nombre",
                  "sanciones", "autoridad", "fecha_efectos_juridicos", "lugar_hechos_departamento", "lugar_hechos_municipio"),
        optional=("segundo_apellido", "segundo_nombre", "calidad_persona", "tipo_identificacion",
                  "nombre_tipo_identificacion", "cargo", "duracion_anos", "duracion_mes", "duracion_dias", "providencia",
                  "numero_proceso", "entidad_sancionado", "entidad_departamento", "entidad_municipio"),
        parser=parse_siri, location_fields=("lugar_hechos_departamento", "lugar_hechos_municipio"),
        capabilities={"discovery": "yes", "detail": "yes", "incremental": "unsupported", "history": "yes",
                      "identifiers": "protected", "deletions": "unsupported", "totals": "yes",
                      "note": "Registro de sanciones e inhabilidades; no representa todas las investigaciones"}),
    "relatoria_pgn": DatasetSpec(
        "SRC-19", "rhun-uf37", "relatoria_pgn", "Relatoría de la Procuraduría", "Procuraduría General de la Nación",
        critical=("tipo_documento", "url_documento", "fecha_documento"),
        optional=("n_mero_documento", "dependencia", "tema", "subtema"),
        parser=parse_relatoria, date_field="fecha_documento",
        capabilities={"discovery": "yes", "detail": "unsupported", "documents": "link_only", "incremental": "unsupported",
                      "note": "Índice documental, no censo de expedientes"}),
    "divipola": DatasetSpec(
        "SRC-20", "gdxc-w37w", "divipola", "DIVIPOLA · Códigos de municipios", "DANE",
        critical=("cod_dpto", "dpto", "cod_mpio", "nom_mpio"), optional=("tipo_municipio",),
        parser=parse_divipola, capabilities={"discovery": "yes", "identifiers": "yes"}),
}


def spec_by_adapter(adapter: str) -> DatasetSpec:
    return SPECS[adapter]
