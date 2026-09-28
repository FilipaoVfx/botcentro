"""Motor de respuestas sin IA (DEC-16; SRS §9, PRD §10).

Cada respuesta se arma con plantillas deterministas a partir de lecturas de la base (`bot_*`) y
de pasajes del índice vectorial, siempre con su fuente. Si no hay evidencia, se dice que no se
encontró en las fuentes cargadas: la ausencia de datos no equivale a cero.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from botcentro.domain.dates import BOGOTA, DateRange
from botcentro.domain.enums import QueryIntent
from botcentro.domain.names import normalize_name
from botcentro.domain.project_ids import ProjectRef, format_ref
from botcentro.insforge.client import RpcClient
from botcentro.query.intent import QueryPlan, fold, plan_query
from botcentro.telegram.render import Section, bold, escape, link

_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_SOURCE_NAMES = {"SRC-01": "Senado · datos abiertos", "SRC-03": "Gacetas del Congreso",
                 "SRC-06": "Cámara · proyectos de ley"}
# Palabras que no forman parte de un nombre propio al buscar congresistas.
_NOT_NAME = frozenset(
    "como voto votos votaron votacion votaciones autor autora autores proyectos proyecto de del la las el los que "
    "ha han presentado presento radicado radico senador senadora representante congresista congresistas "
    "quien quienes sobre para por con en y o un una su sus cuales cual cuantos cuantas partido bancada "
    "informacion dime muestrame dame ver hablame perfil ley leyes asistencia asistio".split())
_REF_ONLY = re.compile(r"^\s*proyecto de (?:acto legislativo|ley)\s+n[úu]mero\s+\d+\s+de\s+\d{4}\s+(?:senado|c[áa]mara)\s*$",
                       re.IGNORECASE)
_FICHA_KEY = re.compile(r"camara-pl:project:(\d+)-(\d{4})$")


class VectorSearch(Protocol):
    def search(self, vector: Sequence[float], *, limit: int = 8, flt: Any = None,
               with_payload: Any = True) -> list[dict[str, Any]]: ...

    def scroll(self, *, offset: Any = None, limit: int = 256, flt: Any = None, with_payload: Any = True,
               with_vector: bool = True) -> tuple[list[dict[str, Any]], Any]: ...


@dataclass
class Answer:
    intent: QueryIntent
    status: str  # answered | needs_clarification | insufficient_evidence
    support: str  # supported | partially_supported | abstained
    sections: list[Section]
    warnings: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)  # proyectos identificados sin ambigüedad


def fmt_date(value: str | date | None) -> str:
    if not value:
        return "sin fecha"
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _ref_json(ref: ProjectRef) -> dict[str, Any]:
    return {"number": ref.number, "year": ref.filing_year,
            "corporation": ref.corporation.value if ref.corporation else None,
            "initiative_type": ref.initiative_type.value if ref.initiative_type else None}


def _safe_link(label: str, url: str | None) -> str:
    return link(label, url) if url and url.startswith(("https://", "http://")) else escape(label)


class AnswerEngine:
    def __init__(self, rpc: RpcClient, vectors: VectorSearch | None, embed_query: Callable[[str], list[float]] | None,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(BOGOTA)) -> None:
        self.rpc = rpc
        self.vectors = vectors
        self.embed_query = embed_query
        self.clock = clock

    # -- entrada -------------------------------------------------------------------------------

    def answer(self, text: str) -> Answer:
        plan = plan_query(text, self.clock())
        if plan.command in {"/start", "/help"}:
            return self._help(plan)
        if plan.command == "/fuentes":
            return self._sources(plan)
        if plan.command == "/privacidad":
            return self._privacy(plan)
        if plan.intent is QueryIntent.UNSUPPORTED:
            return Answer(plan.intent, "answered", "abstained", [Section(None, [
                f"Lo siento: {escape(plan.reason or 'esa consulta está fuera de lo que ofrezco')}.",
                "Puedo mostrarte el estado, los autores, las votaciones y los textos de un proyecto, la agenda "
                "publicada o la actividad de un congresista. Escribe /help para ver ejemplos."])])
        if plan.project_refs:
            return self._projects(plan, text)
        if plan.intent is QueryIntent.AGENDA:
            return self._agenda(plan)
        person = self._person(plan, text)
        if person is not None:
            return person
        return self._documents(plan, text, project_ids=None)

    # -- comandos ------------------------------------------------------------------------------

    def _help(self, plan: QueryPlan) -> Answer:
        return Answer(plan.intent, "answered", "supported", [
            Section("Consulta legislativa de Colombia", [
                "Respondo con datos oficiales del Senado, la Cámara y las Gacetas del Congreso, y cito siempre la fuente.",
            ]),
            Section("Ejemplos", [
                "• <code>PL 178/2025 Senado</code> o <code>/proyecto 396 de 2026 Cámara</code>: ficha, estado, autores y votaciones",
                "• <code>¿Qué dice el PL 396/2026 Cámara sobre el riego?</code>: pasajes de sus textos",
                "• <code>¿Cómo ha votado Paloma Valencia?</code> o <code>proyectos de Juan Carlos Lozada</code>",
                "• <code>agenda de esta semana</code> o <code>/agenda</code>",
                "• <code>proyectos sobre seguridad hídrica</code>: búsqueda en fichas y gacetas",
            ]),
            Section("Límites", [
                "No hago predicciones, no doy asesoría jurídica ni recomiendo votos. /fuentes muestra de dónde "
                "salen los datos y /privacidad qué guardo.",
            ]),
        ])

    def _sources(self, plan: QueryPlan) -> Answer:
        fresh = self.rpc.call("bot_freshness", {}) or {}
        lines = [f"• {escape(_SOURCE_NAMES.get(code, code))} ({code}): actualizado el {fmt_date(ts)}"
                 for code, ts in sorted(fresh.items())]
        lines.append("• Gacetas del Congreso (SRC-03): carga desde el 20 jul 2022 en curso")
        lines.append("<i>Senado: votaciones, asistencia y agenda desde el 1 ene 2022. Cámara: fichas desde 2010.</i>")
        return Answer(plan.intent, "answered", "supported", [
            Section("Fuentes consultadas", lines),
            Section(None, ["El Senado solo publica las votaciones de plenaria registradas en su aplicación; "
                           "la ausencia de un dato no significa que no exista."]),
        ])

    def _privacy(self, plan: QueryPlan) -> Answer:
        return Answer(plan.intent, "answered", "supported", [Section("Privacidad", [
            "• Tu cuenta de Telegram se guarda como un seudónimo (HMAC), no por tu nombre ni tu teléfono.",
            "• El texto de tus preguntas y las respuestas se borran a los 30 días; después solo quedan métricas.",
            "• Las consultas no se envían a servicios de inteligencia artificial externos.",
        ])])

    # -- proyectos -----------------------------------------------------------------------------

    def _projects(self, plan: QueryPlan, text: str) -> Answer:
        resolved = self.rpc.call("bot_resolve_projects", {"p_refs": [_ref_json(r) for r in plan.project_refs[:3]]})
        missing = [plan.project_refs[i] for i, item in enumerate(resolved) if not item["candidates"]]
        ambiguous = [item for item in resolved if len(item["candidates"]) > 1]
        unique = [item["candidates"][0] for item in resolved if len(item["candidates"]) == 1]

        if ambiguous and not unique:
            lines = []
            for item in ambiguous:
                lines += [f"• {bold(c['label'])}: {escape(_clip(c.get('title'), 120) or 'sin título en la fuente')}"
                          for c in item["candidates"]]
            return Answer(plan.intent, "needs_clarification", "abstained", [
                Section("¿A cuál proyecto te refieres?", lines),
                Section(None, ["Escribe el número con año y corporación, por ejemplo <code>PL 178/2025 Senado</code>."]),
            ])
        if not unique:
            labels = ", ".join(format_ref(r) for r in missing)
            return Answer(plan.intent, "insufficient_evidence", "abstained", [Section(None, [
                f"No encontré {bold(labels)} en las fuentes cargadas (Senado datos abiertos y Cámara).",
                "Puede que exista y aún no esté en nuestras fuentes: los proyectos del Senado que no han llegado a "
                "votación en plenaria todavía no se cargan. Revisa el número y el año.",
            ])])

        sections: list[Section] = []
        warnings: list[str] = []
        for candidate in unique[:2]:
            card = self.rpc.call("bot_project_card", {"p_project_id": candidate["project_id"]})
            sections += self._render_card(card)
        if plan.intent in (QueryIntent.DOCUMENT, QueryIntent.HYBRID, QueryIntent.COMPARISON):
            project_ids = [c["project_id"] for c in unique]
            passages = self._passages(text, project_ids, limit=4)
            if passages:
                sections.append(Section("Pasajes relacionados", passages))
            else:
                sections.append(Section(None, ["No encontré pasajes de texto indexados para este proyecto todavía."]))
            if plan.intent is QueryIntent.COMPARISON:
                warnings.append("comparación de versiones no disponible")
                sections.append(Section(None, ["La comparación automática entre versiones del texto aún no está "
                                               "disponible; arriba tienes los pasajes de sus documentos."]))
        if missing:
            sections.append(Section(None, [f"No encontré {escape(', '.join(format_ref(r) for r in missing))}."]))
        return Answer(plan.intent, "answered", "partially_supported" if missing else "supported", sections, warnings,
                      [str(c["project_id"]) for c in unique[:2]])

    def _render_card(self, card: dict[str, Any]) -> list[Section]:
        ids = " · ".join(i["label"] for i in card.get("identifiers") or [])
        profile = card.get("profile") or {}
        head = [bold(ids or "Proyecto")]
        if card.get("title"):
            head.append(escape(_clip(card["title"], 400)))
        if profile.get("object") and profile.get("object") != card.get("title"):
            head.append(f"<i>Objeto:</i> {escape(_clip(profile['object'], 500))}")
        facts = []
        if status := card.get("status"):
            corp = {"camara": " (Cámara)", "senado": " (Senado)"}.get(status.get("corporation") or "", "")
            facts.append(f"• Estado{corp}: {bold(status['raw'])} — reportado al {fmt_date(status.get('observed_at'))}")
        if card.get("origin"):
            facts.append(f"• Origen: {escape(card['origin'])}")
        if profile.get("law_type"):
            facts.append(f"• Tipo: {escape(profile['law_type'])}")
        if profile.get("commissions"):
            facts.append(f"• Comisión: {escape(_clip(profile['commissions'], 120))}")
        filed = [f"Cámara {fmt_date(profile['filed_camara'])}" if profile.get("filed_camara") else "",
                 f"Senado {fmt_date(profile['filed_senado'])}" if profile.get("filed_senado") else ""]
        if any(filed):
            facts.append(f"• Radicado: {escape(', '.join(f for f in filed if f))}")
        sections = [Section(None, head), Section(None, facts)]

        authors = card.get("authors") or []
        if authors:
            total = card.get("authors_total") or len(authors)
            shown = ", ".join(authors[:12]) + (f" y {total - 12} más" if total > 12 else "")
            sections.append(Section(f"Autores ({total})", [escape(shown)]))

        votings = card.get("votings") or []
        if votings:
            lines = []
            for day in dict.fromkeys(v["date"] for v in votings):
                same = [v for v in votings if v["date"] == day]
                subjects = {v.get("subject") for v in same if v.get("subject") and not _REF_ONLY.match(v["subject"])}
                times = f"{len(same)} votaciones · " if len(same) > 1 else ""
                tally = " / ".join(f"Sí {v['yes']} · No {v['no']}" for v in same[:3])
                lines.append(f"• {fmt_date(day)}: {times}{tally}"
                             + (f" — {escape(_clip(', '.join(sorted(subjects)), 90))}" if subjects else ""))
                if len(lines) >= 6:
                    break
            lines.append("<i>Votos nominales de plenaria del Senado registrados en su aplicación.</i>")
            sections.append(Section("Votaciones", lines))

        sources = [_SOURCE_NAMES.get(s, s) for s in card.get("sources") or []]
        foot = []
        if profile.get("link"):
            foot.append(_safe_link("Ficha en la Cámara", profile["link"]))
        if sources:
            foot.append("Fuentes: " + escape(", ".join(sources)))
        sections.append(Section(None, [" · ".join(foot)] if foot else []))
        return sections

    def project_card(self, project_id: str) -> tuple[dict[str, Any], list[Section]]:
        """Ficha por ID canónico (la interfaz nunca vuelve a resolver por número)."""
        card = self.rpc.call("bot_project_card", {"p_project_id": project_id}) or {}
        return card, (self._render_card(card) if card else [])

    def project_documents(self, project_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        """Gacetas indexadas con segmentos enlazados al proyecto, agrupadas por documento."""
        if self.vectors is None:
            return []
        points, _ = self.vectors.scroll(limit=500, with_vector=False, flt={"must": [
            {"key": "project_ids", "match": {"any": [project_id]}}, {"key": "doc_kind", "match": {"value": "gaceta"}}]},
            with_payload=["document_key", "title", "source_url", "segment_kind", "pdf_page_start", "published_on"])
        docs: dict[str, dict[str, Any]] = {}
        for point in points:
            p = point["payload"]
            doc = docs.setdefault(p["document_key"], {"title": p.get("title"), "url": p.get("source_url"),
                                                      "date": p.get("published_on"), "kinds": set(), "pages": set()})
            doc["kinds"].add(p.get("segment_kind") or "otro")
            if p.get("pdf_page_start"):
                doc["pages"].add(int(p["pdf_page_start"]))
        ordered = sorted(docs.values(), key=lambda d: d["date"] or "", reverse=True)
        return ordered[:limit]

    def gacetas_published(self, corporation: str, day: date, *, limit: int = 15) -> list[dict[str, Any]]:
        """Gacetas de una corporación publicadas en una fecha (según el listado de la Imprenta)."""
        if self.vectors is None:
            return []
        points, _ = self.vectors.scroll(limit=1000, with_vector=False, flt={"must": [
            {"key": "doc_kind", "match": {"value": "gaceta"}},
            {"key": "published_on", "match": {"value": day.isoformat()}}]},
            with_payload=["document_key", "title", "source_url", "segment_kind"])
        docs: dict[str, dict[str, Any]] = {}
        for point in points:
            p = point["payload"]
            if not str(p.get("document_key", "")).startswith(f"gaceta:{corporation}:"):
                continue
            doc = docs.setdefault(p["document_key"], {"title": p.get("title"), "url": p.get("source_url"), "kinds": set()})
            doc["kinds"].add(p.get("segment_kind") or "otro")
        return sorted(docs.values(), key=lambda d: d["title"] or "")[:limit]

    def search_actas(self, text: str, *, limit: int = 4) -> list[str]:
        """Pasajes de actas publicadas en gacetas (debates con cobertura limitada, DEC-18)."""
        if self.vectors is None or self.embed_query is None:
            return []
        hits = self.vectors.search(self.embed_query(text), limit=limit, flt={"must": [
            {"key": "doc_kind", "match": {"value": "gaceta"}}, {"key": "segment_kind", "match": {"value": "acta"}}]})
        lines = []
        for hit in hits:
            p = hit["payload"]
            where = f"{p.get('title')}, p. {p.get('pdf_page_start')}"
            lines.append(f"• «{escape(_clip(p.get('text'), 260))}»\n  — {_safe_link(where, p.get('source_url'))}")
        return lines

    # -- congresistas --------------------------------------------------------------------------

    def _person(self, plan: QueryPlan, text: str) -> Answer | None:
        tokens = [t for t in normalize_name(fold(text)).split() if len(t) >= 3 and t not in _NOT_NAME][:5]
        if not tokens or (plan.intent is QueryIntent.DOCUMENT and len(tokens) < 2):
            return None
        groups = self.rpc.call("bot_person_search", {"p_tokens": tokens, "p_limit": 5}) or []
        if not groups:
            return None
        best = groups[0]
        needed = min(len(tokens), 2)
        if best["hits"] < needed:
            return None
        tied = [g for g in groups if g["hits"] == best["hits"]]
        if len(tied) > 1:
            # «Valencia Laserna Paloma» (Senado) y «Paloma Susana Valencia Laserna» (Cámara): si todos los
            # nombres empatados están contenidos en el más completo, se tratan como la misma persona.
            words = [set(normalize_name(g["name"]).split()) for g in tied]
            fullest = max(words, key=len)
            if all(w <= fullest for w in words):
                best = {**max(tied, key=lambda g: len(g["name"])),
                        "person_ids": [pid for g in tied for pid in g["person_ids"]]}
                tied = [best]
        if len(tied) > 1 and best["hits"] < len(tokens):
            return Answer(plan.intent, "needs_clarification", "abstained", [
                Section("¿A quién te refieres?", [f"• {escape(g['name'])}" for g in tied[:6]]),
                Section(None, ["Escribe el nombre completo."]),
            ])
        card = self.rpc.call("bot_person_card", {"p_person_ids": best["person_ids"]})
        return self._render_person(plan, card, merged=len(best["person_ids"]) > 1,
                                   votes_first=bool(re.search(r"\bvot", fold(text))))

    def _render_person(self, plan: QueryPlan, card: dict[str, Any], *, merged: bool, votes_first: bool = False) -> Answer:
        head = [bold(card["name"])]
        if card.get("parties"):
            head.append("Partido: " + escape(", ".join(card["parties"])))
        sections = [Section(None, head)]
        authored = card.get("authored") or []
        if authored:
            lines = [f"• {bold(a['label'])}: {escape(_clip(a.get('title'), 110))}"
                     + (f" <i>({escape(a['status'])})</i>" if a.get("status") else "") for a in authored]
            sections.append(Section(f"Proyectos como autor ({card['authored_total']}), los más recientes", lines))
        yes, no = card.get("votes_yes") or 0, card.get("votes_no") or 0
        if yes or no:
            lines = [f"Votos nominales registrados: Sí {yes} · No {no}"]
            lines += [f"• {fmt_date(v['date'])}: {'Sí' if v['vote'] == 'yes' else 'No'} — {escape(_clip(v.get('subject'), 90))}"
                      for v in card.get("recent_votes") or []]
            votes = Section("Votaciones en plenaria del Senado", lines)
            sections.insert(1, votes) if votes_first else sections.append(votes)
        if len(sections) == 1:
            sections.append(Section(None, ["No tengo autorías ni votaciones registradas para esta persona."]))
        notes = ["Fuentes: " + escape(", ".join(_SOURCE_NAMES.get(s, s) for s in card.get("sources") or []))]
        warnings = []
        if merged:
            warnings.append("identidad agrupada por nombre")
            notes.append("<i>Datos agrupados por nombre entre Senado y Cámara; la identidad aún no está conciliada.</i>")
        sections.append(Section(None, notes))
        return Answer(plan.intent, "answered", "supported", sections, warnings)

    # -- agenda --------------------------------------------------------------------------------

    def _agenda(self, plan: QueryPlan) -> Answer:
        today = self.clock().date()
        period = plan.period or DateRange(today, today + timedelta(days=7))
        if (period.end - period.start).days > 62:
            period = DateRange(period.start, period.start + timedelta(days=62))
        data = self.rpc.call("bot_agenda", {"p_from": period.start.isoformat(), "p_to": period.end.isoformat(),
                                            "p_limit": 25})
        label = f"{fmt_date(period.start)} – {fmt_date(period.end)}" if period.start != period.end else fmt_date(period.start)
        items = data.get("items") or []
        if not items:
            return Answer(QueryIntent.AGENDA, "insufficient_evidence", "abstained", [Section(None, [
                f"No hay agenda publicada para {escape(label)} en las fuentes cargadas.",
                f"La agenda más reciente que tengo es del {fmt_date(data.get('latest_date'))}.",
            ])])
        lines = [f"• {fmt_date(i['date'])}: {escape(_clip(i.get('title'), 160))}" for i in items]
        if data.get("total", 0) > len(items):
            lines.append(f"… y {data['total'] - len(items)} más.")
        return Answer(QueryIntent.AGENDA, "answered", "supported", [
            Section(f"Agenda publicada · {label}", lines),
            Section(None, ["<i>Fuente: Senado · datos abiertos. Publicar la agenda no confirma que la sesión se celebre.</i>"]),
        ], ["agenda no confirma sesión"])

    # -- documentos ----------------------------------------------------------------------------

    def _search(self, text: str, project_ids: Sequence[str] | None, limit: int) -> list[dict[str, Any]]:
        if self.vectors is None or self.embed_query is None:
            return []
        flt = {"must": [{"key": "project_ids", "match": {"any": list(project_ids)}}]} if project_ids else None
        return self.vectors.search(self.embed_query(text), limit=limit, flt=flt)

    def _passages(self, text: str, project_ids: Sequence[str] | None, *, limit: int) -> list[str]:
        lines = []
        for hit in self._search(text, project_ids, limit * 3):
            p = hit["payload"]
            if p.get("doc_kind") != "gaceta":
                continue
            where = f"{p.get('title') or p.get('document_key')}, p. {p.get('pdf_page_start')}"
            lines.append(f"• «{escape(_clip(p.get('text'), 280))}»\n  — {_safe_link(where, p.get('source_url'))}")
            if len(lines) >= limit:
                break
        return lines

    def _documents(self, plan: QueryPlan, text: str, project_ids: Sequence[str] | None) -> Answer:
        hits = self._search(text, project_ids, 30)
        projects: list[str] = []
        seen: set[str] = set()
        passages: list[str] = []
        for hit in hits:
            p = hit["payload"]
            if p.get("doc_kind") == "ficha" and p.get("document_key") not in seen and len(projects) < 6:
                seen.add(p["document_key"])
                key = _FICHA_KEY.search(p["document_key"])
                number = f"{bold(f'Cámara {key.group(1)}/{key.group(2)}')} · " if key else ""
                title = re.search(r"^Título: (.+)$", p.get("text") or "", re.MULTILINE)
                projects.append(f"• {number}{_safe_link(p.get('title') or 'Proyecto', p.get('source_url'))}: "
                                f"{escape(_clip(title.group(1) if title else p.get('text'), 160))}")
            elif p.get("doc_kind") == "gaceta" and len(passages) < 3:
                where = f"{p.get('title')}, p. {p.get('pdf_page_start')}"
                passages.append(f"• «{escape(_clip(p.get('text'), 260))}»\n  — {_safe_link(where, p.get('source_url'))}")
        if not projects and not passages:
            return Answer(plan.intent, "insufficient_evidence", "abstained", [Section(None, [
                "No encontré información sobre eso en las fichas ni en las gacetas cargadas.",
                "Prueba con otras palabras o con el número del proyecto (por ejemplo <code>PL 178/2025 Senado</code>).",
            ])])
        sections = []
        if projects:
            sections.append(Section("Proyectos relacionados", projects))
        if passages:
            sections.append(Section("Pasajes en gacetas", passages))
        sections.append(Section(None, ["<i>Resultados por similitud de significado; escribe el número de un proyecto "
                                       "para ver su ficha completa.</i>"]))
        return Answer(plan.intent, "answered", "partially_supported", sections, ["búsqueda por similitud"])


def plain_text(answer: Answer) -> str:
    """Texto sin etiquetas para answer_records (retención de 30 días)."""
    import html

    from botcentro.telegram.render import render_sections
    return html.unescape(re.sub(r"<[^>]+>", "", "\n\n".join(render_sections(answer.sections))))
