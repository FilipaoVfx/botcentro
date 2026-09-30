"""Muestra de verificación de una fuente en modo sombra (investigaciones §24.6, SRC-15).

Antes de publicar una fuente se compara a mano una muestra contra el portal oficial. Este módulo:

1. elige una muestra estratificada por municipio: el mayor valor, un contrato modificado, un estado
   poco frecuente, un tipo distinto de prestación de servicios y el resto al azar (semilla fija, así
   la muestra es reproducible);
2. vuelve a consultar cada contrato en datos.gov.co y lo compara campo a campo con lo guardado
   (estado, valor, fecha de firma, NIT de la entidad, identidad del contratista, tipo, enlace);
3. escribe una lista de chequeo (Markdown y CSV) para la verificación manual en el portal SECOP.

La salida contiene nombres de contratistas persona (dato público de la fuente, pero personal):
se escribe en `var/`, fuera del repositorio, y no incluye documentos en claro.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from botcentro.investigations.datasets import SPECS, parse_secop2_contract
from botcentro.investigations.socrata import Condition, Query, SocrataClient

FIELDS = ("estado", "valor_inicial", "fecha_firma", "nit_entidad", "contratista", "tipo", "enlace")
RARE = ("cedido", "Suspendido", "Cerrado", "terminado", "Aprobado")


@dataclass
class SampleItem:
    reason: str
    native_id: str
    territory: str
    stored: dict[str, Any]
    source: dict[str, Any] | None = None
    diffs: list[str] = field(default_factory=list)
    fetch_error: str | None = None


SAMPLE_SQL = """
select c.native_id, t.name as territory, v.status_original, v.value_initial, v.value_current, v.signed_on,
       v.contract_type, v.modality, c.object, c.official_url, e.display_name as entity,
       (select value_public from public.actor_identifiers i where i.actor_id = e.id and i.issuer = 'DIAN' limit 1) as entity_nit,
       k.display_name as contractor, k.actor_type as contractor_type,
       (select coalesce(value_public, value_hmac) from public.actor_identifiers i where i.actor_id = k.id limit 1) as contractor_key,
       (select value_masked from public.actor_identifiers i where i.actor_id = k.id limit 1) as contractor_masked,
       (select count(*) from public.contract_versions x where x.contract_id = c.id) as versions
  from public.contracts c
  join public.sources s on s.id = c.source_id and s.code = %(code)s
  join public.contract_versions v on v.id = c.current_version_id
  left join public.territories t on t.id = c.territory_id
  left join public.actors e on e.id = c.entity_actor_id
  left join public.actors k on k.id = c.contractor_actor_id
"""


def choose(rows: list[dict[str, Any]], *, per_territory: int = 7, seed: int = 20260930) -> list[tuple[str, dict[str, Any]]]:
    """(motivo, fila) estratificado por municipio; determinista para una semilla."""
    rng = random.Random(seed)
    picked: list[tuple[str, dict[str, Any]]] = []
    by_territory: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r["native_id"]):
        by_territory.setdefault(r["territory"] or "sin territorio", []).append(r)
    for territory in sorted(by_territory):
        pool = by_territory[territory]
        chosen: list[tuple[str, dict[str, Any]]] = []

        def take(reason: str, candidates: list[dict[str, Any]]) -> None:
            ids = {c["native_id"] for _, c in chosen}
            candidates = [c for c in candidates if c["native_id"] not in ids]
            if candidates and len(chosen) < per_territory:
                chosen.append((reason, candidates[0]))

        take("mayor valor", sorted(pool, key=lambda r: r["value_current"] or Decimal(0), reverse=True))
        take("estado Modificado", [r for r in pool if r["status_original"] == "Modificado"])
        take("estado poco frecuente", [r for r in pool if r["status_original"] in RARE])
        take("tipo distinto de prestación de servicios", [r for r in pool if r["contract_type"] != "Prestación de servicios"])
        take("contratista persona", [r for r in pool if r["contractor_type"] == "persona"])
        rest = pool[:]
        rng.shuffle(rest)
        while len(chosen) < per_territory and rest:
            take("al azar", [rest.pop()])
        picked += chosen
    return picked


def stored_view(r: dict[str, Any]) -> dict[str, Any]:
    return {"estado": r["status_original"], "valor_inicial": _dec(r["value_initial"]),
            "fecha_firma": r["signed_on"].isoformat() if r["signed_on"] else None, "nit_entidad": r["entity_nit"],
            "contratista": r["contractor_key"], "tipo": r["contract_type"], "enlace": r["official_url"],
            # Solo para la lista de chequeo (no se comparan):
            "entidad": r["entity"], "contratista_nombre": r["contractor"] or "no informado",
            "contratista_id": (f"doc. {r['contractor_masked']}" if r["contractor_type"] == "persona"
                               else f"NIT {r['contractor_key']}") if r["contractor_key"] else "sin documento",
            "objeto": " ".join((r["object"] or "").split())[:220]}


def source_view(row: dict[str, Any], key: bytes) -> dict[str, Any]:
    v = parse_secop2_contract(row, key).value
    contractor = v["contractor"]
    return {"estado": v["status"], "valor_inicial": _dec(v["value_initial"]), "fecha_firma": v["signed_on"],
            "nit_entidad": v["entity"]["nit"], "contratista": contractor.get("id_public") or contractor.get("id_hmac"),
            "tipo": v["contract_type"], "enlace": v["url"]}


def _dec(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(Decimal(str(value)).normalize())


def compare(items: list[SampleItem], client: SocrataClient, key: bytes, dataset: str = "jbjy-vk9h") -> None:
    spec = SPECS["secop2_contracts"]
    for item in items:
        query = Query(allowed_fields=spec.fields, conditions=[Condition("id_contrato", "=", item.native_id)], limit=5)
        result = client.fetch_page(spec.code, dataset, query)
        if result.errors:
            item.fetch_error = result.errors[0].safe_message
            continue
        if not result.items:
            item.fetch_error = "ya no aparece en la fuente"
            continue
        item.source = source_view(result.items[0], key)
        item.diffs = [f for f in FIELDS if (item.stored.get(f) or None) != (item.source.get(f) or None)]


def write_report(items: list[SampleItem], out_dir: Path, *, today: date) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"muestra-secop-{today.isoformat()}.md"
    csv_path = out_dir / f"muestra-secop-{today.isoformat()}.csv"
    same = sum(1 for i in items if i.source and not i.diffs)
    lines = [f"# Muestra de verificación SECOP II (SRC-15) · {today.isoformat()}", "",
             f"{len(items)} contratos: {same} coinciden campo a campo con datos.gov.co hoy; "
             f"{sum(1 for i in items if i.diffs)} con diferencias; {sum(1 for i in items if i.fetch_error)} sin consulta.",
             "", "Diferencias en `estado` o `valor` pueden ser cambios reales de la fuente desde la carga (se versionan "
             "en la próxima actualización); diferencias en NIT, contratista, fecha o enlace indican un error de mapeo.", "",
             "## Verificación manual en el portal SECOP", "",
             "Para cada contrato: abrir el enlace y confirmar entidad, contratista, valor, estado y fecha de firma. "
             "Marcar la casilla si todo coincide; si no, anotar qué campo difiere.", ""]
    for n, i in enumerate(items, start=1):
        s = i.stored
        check = "sin diferencias" if i.source and not i.diffs else (
            f"difiere: {', '.join(i.diffs)}" if i.diffs else f"no verificado: {i.fetch_error}")
        lines += [f"### {n}. {i.native_id} · {i.territory} · {i.reason}", "",
                  f"- [ ] Coincide en el portal · [Abrir en SECOP]({s['enlace']})",
                  f"- Entidad: {s['entidad']} (NIT {s['nit_entidad'] or 'no informado'})",
                  f"- Contratista: {s['contratista_nombre']} ({s['contratista_id']})",
                  f"- Valor inicial: ${int(Decimal(s['valor_inicial'] or 0)):,}".replace(",", ".") + f" · Estado: {s['estado']}"
                  f" · Firma: {s['fecha_firma'] or 'sin fecha'} · Tipo: {s['tipo']}",
                  f"- Objeto: {s['objeto']}",
                  f"- Comparación automática con datos.gov.co: **{check}**"]
        for f in i.diffs:
            lines.append(f"  - {f}: guardado `{s.get(f)}` · fuente hoy `{(i.source or {}).get(f)}`")
        lines.append("")
    md.write_text("\n".join(lines))
    with csv_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["n", "id_contrato", "municipio", "motivo", "enlace", "entidad", "contratista", "valor_inicial",
                    "estado", "fecha_firma", "comparacion_automatica", "verificado_manual", "nota"])
        for n, i in enumerate(items, start=1):
            s = i.stored
            w.writerow([n, i.native_id, i.territory, i.reason, s["enlace"], s["entidad"], s["contratista_nombre"],
                        s["valor_inicial"], s["estado"], s["fecha_firma"],
                        "ok" if i.source and not i.diffs else (";".join(i.diffs) or i.fetch_error), "", ""])
    return md, csv_path
