"""Casos de uso de la interfaz: acción + contexto → vista (centrorequirement.md §4, §6–§10, §19).

La capa es síncrona y sin efectos de negocio al renderizar: consulta servicios de dominio
(`AnswerEngine` y lecturas `bot_*`) y devuelve un `ViewModel` con las acciones válidas. Las
capacidades aún no implementadas no aparecen en los menús; si se piden por texto, se explica
el límite y se ofrece una alternativa (UI-F05, UI-F39).

Listas: se materializan hasta 200 IDs ordenados en un `ResultSet` (Redis, 30 min). La
paginación y los ordinales («el segundo») usan esos IDs, nunca una búsqueda nueva (UI-F08/F09).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from botcentro.domain.dates import BOGOTA, DateRange, format_local_date, parse_spanish_date, resolve_relative_period
from botcentro.query.engine import AnswerEngine, fmt_date
from botcentro.telegram.render import Section, bold, escape, link, render_sections
import re
from html import unescape as html_unescape

from botcentro.telegram_ui.contracts import (Button, Frame, Intent, PageInfo, ResultSet, SessionContext, UiAction,
                                             ViewModel)
from botcentro.telegram_ui.investigations import INVESTIGATION_INTENTS, InvestigationViews

HOME_BUTTON = Button(label="🏠 Inicio", intent=Intent.HOME)
HELP_BUTTON = Button(label="❓ Ayuda", intent=Intent.HELP)
BACK_BUTTON = Button(label="⬅️ Volver", intent=Intent.BACK)
IMPLEMENTED = frozenset({
    Intent.HOME, Intent.HELP, Intent.SOURCES, Intent.AGENDA, Intent.BACK, Intent.CANCEL, Intent.REFRESH,
    Intent.QUESTION, Intent.PROJECTS_LIST, Intent.PROJECTS_SEARCH, Intent.PROJECTS_FILTERS, Intent.FILTER_SET,
    Intent.SEARCH_PROMPT, Intent.PAGE, Intent.PROJECT_OPEN, Intent.PROJECT_PARTICIPANTS, Intent.ORDINAL,
    Intent.VOTINGS, Intent.DOCUMENTS, Intent.DAY_OVERVIEW, Intent.PROJECT_TIMELINE, Intent.DISCUSSIONS,
    Intent.VOTING_OPEN, Intent.VOTINGS_PERSON, Intent.VOTE_PERSON_PROMPT, Intent.EVIDENCE,
    Intent.ACTA_SEARCH, Intent.ACTA_SEARCH_PROMPT,
})
_NO_EVIDENCE_VIEWS = {"home", "help", "sources", "evidence", "cancel", "filters", "search_prompt", "unavailable",
                      "clarify", "expired", "subscribe_prompt", "subscribed", "subscriptions", "confirm", "coverage",
                      "territory_search"}
_ENGINE_FALLBACK = frozenset({Intent.PROJECTS_SEARCH})
_PENDING_LABELS = {
    Intent.DAY_OVERVIEW: "la portada del día", Intent.DISCUSSIONS: "los debates legislativos",
    Intent.DOCUMENTS: "el explorador general de documentos",
    Intent.COMPARE: "la comparación de versiones", Intent.EVIDENCE: "las fuentes de cada respuesta",
}
_PENDING_HINT = {
    Intent.DAY_OVERVIEW: "Mientras tanto puedes pedir «agenda de esta semana».",
    Intent.DOCUMENTS: "Abre un proyecto y toca «Documentos».",
}
_LINK_RE = re.compile(r'<a href="([^"]+)">([^<]+)</a>')
RECENT_DAYS = 30
PERIODS = {"30": ("30 días", 30), "90": ("90 días", 90), "365": ("1 año", 365), "all": ("Todo", None)}
CORPORATIONS = {"all": "Ambas", "senado": "Senado", "camara": "Cámara"}
TYPES = {"all": "Todos", "proyecto_ley": "Ley", "proyecto_acto_legislativo": "Acto legislativo"}


def _session_groups(videos: list[dict[str, Any]], actas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una entrada por sesión (fecha, corporación, cuerpo) con su video oficial y/o su acta en la Gaceta.
    Solo se unen si coinciden los tres datos; un acta sin cuerpo identificado queda aparte."""
    groups: dict[tuple, dict[str, Any]] = {}
    for g in _video_groups(videos):
        groups[(str(g.get("session_date"))[:10], g.get("corporation"), g.get("body_key") or g.get("title"))] = g
    for a in actas:
        key = (str(a.get("session_date"))[:10], a.get("corporation"), a.get("body_key") or f"acta:{a['document_key']}")
        g = groups.setdefault(key, {"session_date": a.get("session_date"), "corporation": a.get("corporation"),
                                    "body": a.get("body"), "body_key": a.get("body_key"), "kind": "sesion", "urls": []})
        g.setdefault("actas", []).append(a)
    return sorted(groups.values(), key=lambda g: (str(g.get("session_date")), g.get("body_key") == "plenaria"), reverse=True)


_PAGE_HEADER = re.compile(r"P[áa]gina\s+\d+\s+\w+,?\s+\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\s+G\s*aceta\s+del\s+con\s*greso\s+\d+",
                          re.IGNORECASE)


def _snippet(raw: str | None) -> str:
    """Fragmento legible: sin saltos de página ni cabeceras de la gaceta, guiones de corte unidos; las
    coincidencias (⟦ ⟧) en negrita después de escapar el texto."""
    text = (raw or "").replace("\x0c", " ")
    text = _PAGE_HEADER.sub(" ", text)
    text = re.sub(r"(\w) ?-\s*\n\s*(\w)", r"\1\2", text)
    text = " ".join(text.split())
    return escape(text).replace("⟦", "<b>").replace("⟧", "</b>")


def _acta_link(a: dict[str, Any]) -> str:
    page = f", p. {a['pdf_page']}" if a.get("pdf_page") else ""
    return link(f"📄 Acta {a['acta_number']}/{a['acta_year']} · Gaceta {a['gaceta']}{page}", a["url"])


def _video_groups(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una entrada por sesión (fecha, corporación, cuerpo): la misma sesión puede estar en dos canales."""
    groups: dict[tuple, dict[str, Any]] = {}
    for v in videos:
        key = (v.get("session_date"), v.get("corporation"), v.get("body_key") or v.get("title"))
        g = groups.setdefault(key, {**v, "urls": []})
        g["urls"].append((v.get("channel"), v["url"]))
        g["topic"] = g.get("topic") or v.get("topic")
    return list(groups.values())


def _video_label(v: dict[str, Any]) -> str:
    where = {"senado": "Senado", "camara": "Cámara", "congreso": "Congreso"}.get(v.get("corporation") or "", "Congreso")
    what = "Audiencia pública" if v.get("kind") == "audiencia" else (v.get("body") or "Sesión")
    if v.get("kind") == "audiencia" and v.get("body"):
        what += f" · {v['body']}"
    return f"{what} ({where})"


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class UiApplication:
    def __init__(self, engine: AnswerEngine, *, is_available: Callable[[Intent], bool] | None = None,
                 today: Callable[[], date] | None = None, investigations: InvestigationViews | None = None) -> None:
        self.engine = engine
        self.rpc = engine.rpc
        self.investigations = investigations
        base = is_available or (lambda intent: intent in IMPLEMENTED)
        # Investigaciones: visibles solo con su bandera encendida (FEATURE_CASES / FEATURE_SUBSCRIPTIONS).
        self.is_available = lambda intent: (investigations.available(intent) if investigations is not None
                                            and intent in INVESTIGATION_INTENTS else base(intent))
        self.today = today or (lambda: datetime.now(BOGOTA).date())

    # -- entrada -------------------------------------------------------------------------------

    def handle(self, action: UiAction, ctx: SessionContext, result_set: ResultSet | None = None, *,
               user_hash: str | None = None) -> ViewModel:
        """Aplica la acción sobre el contexto (se modifica en sitio) y devuelve la vista. `user_hash` es la
        identidad seudónima de quien actúa (seguimientos); nunca se toma de parámetros del botón."""
        intent = action.intent
        if intent is Intent.BACK:
            frame = ctx.pop()
            if frame is None:
                return self._home(ctx)
            return self._render(UiAction(intent=frame.intent, entry_point="button", parameters=frame.params), ctx,
                                result_set, push=False, user_hash=user_hash)
        if intent is Intent.REFRESH:
            if ctx.current is None:
                return self._home(ctx)
            return self._render(UiAction(intent=ctx.current.intent, entry_point="button",
                                         parameters=ctx.current.params), ctx, result_set, push=False,
                                user_hash=user_hash)
        if intent is Intent.CANCEL:
            ctx.pending_clarification = None
            return ViewModel(view_type="cancel", title="Listo", blocks=["Cancelé la selección en curso."],
                             rows=[[HOME_BUTTON, HELP_BUTTON]])
        return self._render(action, ctx, result_set, push=True, user_hash=user_hash)

    def _track_evidence(self, view: ViewModel, ctx: SessionContext) -> ViewModel:
        """Fuentes de esta respuesta: enlaces citados en la vista más las fuentes declaradas (CU-11)."""
        if view.view_type in _NO_EVIDENCE_VIEWS:
            return view
        seen = {e.get("url") for e in view.evidence}
        for url, label in _LINK_RE.findall("\n".join(view.blocks)):
            url = html_unescape(url)
            if url not in seen:
                seen.add(url)
                view.evidence.append({"label": html_unescape(label), "url": url})
        if view.evidence:
            ctx.last_evidence = view.evidence[:15]
            ctx.last_evidence_title = view.title
            if not any(b.intent is Intent.EVIDENCE for row in view.rows for b in row):
                view.rows.insert(max(len(view.rows) - 1, 0), [Button(label="🔗 Fuentes", intent=Intent.EVIDENCE)])
        return view

    def _render(self, action: UiAction, ctx: SessionContext, result_set: ResultSet | None, *, push: bool,
                user_hash: str | None = None) -> ViewModel:
        intent = action.intent
        params = dict(action.parameters)
        # Captura de búsqueda: tras «🔎 Buscar», un texto breve sin números es el término buscado;
        # cualquier intención explícita distinta la suspende (§19.3).
        if ctx.pending_clarification and ctx.pending_clarification.get("expect") in ("search", "discussion",
                                                                                     "vote_person", "territory", "acta_search"):
            expect = ctx.pending_clarification["expect"]
            scope = dict(ctx.pending_clarification.get("scope") or {})
            ctx.pending_clarification = None
            text = params.get("text", "")
            if expect == "acta_search" and intent is Intent.QUESTION and text:
                intent, params = Intent.ACTA_SEARCH, {"query": text, "text": text, **scope}
            elif intent is Intent.QUESTION and text and len(text.split()) <= 6 and not any(c.isdigit() for c in text):
                intent, params = {"search": (Intent.PROJECTS_SEARCH, {"query": text, "text": text}),
                                  "discussion": (Intent.DISCUSSIONS, {"query": text, "text": text}),
                                  "vote_person": (Intent.VOTINGS_PERSON, {"name": text}),
                                  "territory": (Intent.TERRITORY_RESOLVE, {"name": text, "text": text}),
                                  "acta_search": (Intent.ACTA_SEARCH, {"query": text, "text": text, **scope})}[expect]
        if not self.is_available(intent) and intent in _ENGINE_FALLBACK and params.get("text"):
            intent, params = Intent.QUESTION, {"text": params["text"]}
        if not self.is_available(intent):
            if self.investigations is not None and intent in INVESTIGATION_INTENTS:
                return self.investigations.unavailable(intent)
            return self._not_yet(intent)

        if intent in INVESTIGATION_INTENTS and self.investigations is not None:
            view, push = self.investigations.render(intent, params, ctx, result_set, user_hash)
        elif intent is Intent.HOME:
            ctx.navigation_stack = []
            ctx.applied_filters = {}
            view = self._home(ctx)
        elif intent is Intent.HELP:
            view = self._from_answer("help", "Ayuda", self.engine.answer("/help"), ctx, [[HOME_BUTTON]])
        elif intent is Intent.SOURCES:
            view = self._from_answer("sources", "Fuentes disponibles", self.engine.answer("/fuentes"), ctx,
                                     [[HOME_BUTTON]])
        elif intent is Intent.AGENDA:
            view = self._agenda(params)
        elif intent is Intent.DAY_OVERVIEW:
            view = self._day(params)
        elif intent is Intent.PROJECT_TIMELINE:
            view = self._timeline(str(params.get("project_id") or ctx.active_project_id or ""), ctx)
        elif intent is Intent.DISCUSSIONS:
            view = self._discussions(params, ctx)
        elif intent in (Intent.PROJECTS_LIST, Intent.PROJECTS_SEARCH, Intent.FILTER_SET):
            view = self._project_list(intent, params, ctx)
            if view.view_type == "search_fallback":
                push = True
        elif intent is Intent.PROJECTS_FILTERS:
            view = self._filters(ctx)
        elif intent is Intent.SEARCH_PROMPT:
            ctx.pending_clarification = {"expect": "search"}
            view = ViewModel(view_type="search_prompt", title="Buscar", blocks=[
                f"{bold('🔎 Buscar proyectos')}\nEscribe un tema o palabras del título, por ejemplo "
                "<code>salud mental</code> o <code>agua</code>.\nPara un proyecto concreto escribe su número: "
                "<code>PL 178/2025 Senado</code>."], rows=[[Button(label="✖️ Cancelar", intent=Intent.CANCEL)]])
            push = False
        elif intent is Intent.PAGE:
            page = int(params.get("page", 1))
            view = (self._votings_page(result_set, page, ctx) if result_set is not None and result_set.kind == "votings"
                    else self._list_page(result_set, page, ctx))
            push = False
        elif intent is Intent.ORDINAL:
            return self._ordinal(int(params.get("position", 0)), ctx, result_set)
        elif intent is Intent.PROJECT_OPEN:
            view = self._project_card(str(params["project_id"]), ctx)
        elif intent is Intent.VOTINGS:
            # Texto «votaciones» con una ficha abierta usa ese proyecto (y lo muestra en el título);
            # /votaciones y el botón del inicio abren el explorador general (§5.1).
            project_id = params.get("project_id") or (ctx.active_project_id if action.entry_point == "text" else None)
            view = self._votings_list(params, ctx, project_id=project_id)
        elif intent is Intent.VOTINGS_PERSON:
            view = self._votings_person(params, ctx)
        elif intent is Intent.VOTE_PERSON_PROMPT:
            ctx.pending_clarification = {"expect": "vote_person"}
            view = ViewModel(view_type="search_prompt", title="Persona", blocks=[
                f"{bold('👤 Votos de una persona')}\nEscribe su nombre, por ejemplo <code>Paloma Valencia</code>."],
                rows=[[Button(label="✖️ Cancelar", intent=Intent.CANCEL)]])
            push = False
        elif intent is Intent.VOTING_OPEN:
            view = self._voting_detail(str(params["voting_id"]), ctx)
        elif intent is Intent.EVIDENCE:
            return self._evidence(ctx)
        elif intent is Intent.ACTA_SEARCH_PROMPT:
            scope = {k: params[k] for k in ("document_key", "page_from", "acta_number", "acta_year", "label") if params.get(k)}
            ctx.pending_clarification = {"expect": "acta_search", "scope": scope}
            where = f"el {params['label']}" if params.get("label") else "las actas de las sesiones del Congreso"
            view = ViewModel(view_type="search_prompt", title="Buscar en actas", blocks=[
                f"{bold('🔎 Buscar en ' + escape(where))}\nEscribe qué buscas: un nombre entre comillas "
                "(<code>\"Paloma Valencia\"</code>), un tema (<code>reforma pensional</code>) o una palabra "
                "(<code>votación</code>)."], rows=[[Button(label="✖️ Cancelar", intent=Intent.CANCEL)]])
            push = False
        elif intent is Intent.ACTA_SEARCH:
            view = self._acta_search(params)
        elif intent in (Intent.PROJECT_PARTICIPANTS, Intent.DOCUMENTS):
            project_id = params.get("project_id") or (ctx.active_project_id if params.get("contextual")
                                                      or action.entry_point == "text" else None)
            if not project_id:
                if intent is Intent.PROJECT_PARTICIPANTS:
                    return ViewModel(view_type="clarify", title="¿De qué proyecto?", status="partial", blocks=[
                        "¿De qué proyecto? Escribe su número (<code>PL 178/2025 Senado</code>) o abre uno desde "
                        "📚 Proyectos."], rows=[[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST), HOME_BUTTON]])
                return self._not_yet(intent)
            params["project_id"] = project_id
            view = {Intent.PROJECT_PARTICIPANTS: self._participants,
                    Intent.DOCUMENTS: self._documents}[intent](str(project_id), ctx)
        else:  # pregunta libre: el motor sin IA resuelve proyecto, persona, agenda o búsqueda
            answer = self.engine.answer(params.get("text", ""))
            rows: list[list[Button]] = []
            if len(answer.project_ids) == 1:
                ctx.active_project_id = answer.project_ids[0]
                actions = self._project_actions(answer.project_ids[0])
                rows += [actions[:2], actions[2:]]
            if answer.person_ids:
                ctx.active_person_ids = answer.person_ids
                rows.append([Button(label="🗳 Todas sus votaciones", intent=Intent.VOTINGS_PERSON,
                                    params={"person_ids": answer.person_ids[:6]})])
            view = self._from_answer("answer", "Respuesta", answer, ctx, rows + [[HOME_BUTTON, HELP_BUTTON]])
            view.active_project_id = ctx.active_project_id if answer.project_ids else None
        if push:
            ctx.push(Frame(intent=intent, params=params, title=view.title))
        return self._track_evidence(view, ctx)

    # -- inicio y ayudas -----------------------------------------------------------------------

    def _session_status_block(self, corp: str, corp_name: str, ref: date) -> tuple[list[str], list[Button]]:
        """«Última sesión» y «Próxima sesión» con fuente y significado (bot_session_status)."""
        try:
            st = self.rpc.call("bot_session_status", {"p_corporation": corp, "p_today": ref.isoformat()}) or {}
        except Exception:  # noqa: BLE001 — la vista del día no depende de este bloque
            return [], []
        lines, buttons = ["", bold("📅 Sesiones")], []
        held: list[date] = []
        if vs := st.get("last_vote_session"):
            d = date.fromisoformat(str(vs["date"])[:10]); held.append(d)
            lines.append(f"• Última plenaria con votaciones registradas: {format_local_date(d)} ({vs['votings']} votaciones)")
        if vv := st.get("last_video_session"):
            d = date.fromisoformat(str(vv["date"])[:10]); held.append(d)
            what = "Audiencia pública" + (f" · {vv['body']}" if vv.get("body") else "") if vv.get("kind") == "audiencia" \
                else (vv.get("body") or "Sesión")
            topic = f" — {escape(_clip(vv['topic'], 110))}" if vv.get("topic") else ""
            lines.append(f"• Última sesión con video oficial: {link(what, vv['url'])}, {format_local_date(d)}{topic}")
        if not held:
            lines.append(f"• No tengo sesiones celebradas de la {corp_name} registradas en las fuentes cubiertas.")
        if nxt := st.get("next_agenda"):
            d = date.fromisoformat(str(nxt["date"])[:10])
            titles = "; ".join(_clip(t, 70) for t in (nxt.get("titles") or [])[:3])
            lines.append(f"• Próxima en la agenda publicada: {format_local_date(d)}" + (f" — {escape(titles)}" if titles else ""))
            lines.append("<i>Programada: publicar la agenda no confirma que la sesión se celebre.</i>")
            buttons.append(Button(label="📅 Próxima sesión", intent=Intent.DAY_OVERVIEW,
                                  params={"corporation": corp, "date": d.isoformat()}))
        elif st.get("agenda_covered"):
            checked = st.get("agenda_checked_at")
            when = ""
            if checked:
                at = datetime.fromisoformat(str(checked)).astimezone(BOGOTA)
                when = f" (última consulta: {format_local_date(at.date())}, {at:%H:%M})"
            lines.append(f"• Próxima: el {corp_name} no ha publicado agenda para las próximas tres semanas{when}.")
            pattern = (st.get("weekday_pattern") or {})
            by_day, total = pattern.get("by_weekday") or {}, pattern.get("days") or 0
            if total:
                names = {"1": "lunes", "2": "martes", "3": "miércoles", "4": "jueves", "5": "viernes", "6": "sábado", "7": "domingo"}
                top = sorted(by_day.items(), key=lambda kv: -kv[1])[:2]
                lines.append(f"<i>En el último año las plenarias con votación fueron sobre todo "
                             f"{' y '.join(f'{names[k]} ({v})' for k, v in top)}, de {total} días con sesión; "
                             "es un patrón, no una programación.</i>")
        else:
            lines.append(f"• Próxima: la {corp_name} no publica su agenda en nuestras fuentes.")
        if held:
            last = max(held)
            if last != ref:
                buttons.insert(0, Button(label="📅 Última sesión", intent=Intent.DAY_OVERVIEW,
                                         params={"corporation": corp, "date": last.isoformat()}))
        return lines, buttons

    def _actas(self, corporation: str | None, start: date, end: date) -> list[dict[str, Any]]:
        """Actas de sesiones en la Gaceta (SRC-03). Si la fuente no responde, la vista sigue sin ellas."""
        try:
            return self.rpc.call("bot_session_actas", {"p_corporation": corporation, "p_from": start.isoformat(),
                                                       "p_to": end.isoformat()}) or []
        except Exception:  # noqa: BLE001
            return []

    def _acta_search(self, params: dict[str, Any]) -> ViewModel:
        query = str(params.get("query") or "").strip()
        scoped = bool(params.get("document_key"))
        try:
            data = self.rpc.call("bot_acta_search", {"p_query": query, "p_document_key": params.get("document_key"),
                                                     "p_page_from": params.get("page_from"), "p_page_to": None,
                                                     "p_limit": 12 if scoped else 6}) or {}
        except Exception:  # noqa: BLE001 — falla de la fuente: se dice, no se presenta como «sin resultados»
            return ViewModel(view_type="unavailable", title="Búsqueda no disponible", status="unavailable",
                             blocks=["No pude consultar las actas en este momento. Inténtalo de nuevo en unos minutos."],
                             rows=[[HOME_BUTTON]])
        items = data.get("items") or []
        if scoped and params.get("acta_number"):  # solo la acta pedida, aunque la gaceta traiga otras
            items = [i for i in items if (i.get("session") or {}).get("acta_number") == str(params["acta_number"])
                     and str((i.get("session") or {}).get("acta_year")) == str(params.get("acta_year"))]
        where = escape(params["label"]) if scoped and params.get("label") else "las actas del Congreso"
        mode = {"frase": "frase exacta", "palabras": "todas las palabras"}.get(data.get("mode") or "", "")
        lines = [bold(f"🔎 «{escape(query)}» en {where}")]
        if items:
            total = len(items) if scoped else data.get("known_total", len(items))
            extra = "" if scoped else f" en {data.get('documents')} gacetas"
            lines.append(f"<i>{total} fragmento(s){extra}" + (f" · {mode}" if mode else "") + "; los más pertinentes primero.</i>")
        lines.append("")
        rows: list[list[Button]] = []
        # Una entrada por sesión (acta) con su mejor fragmento; dentro de una acta, cada fragmento por separado.
        grouped: list[dict[str, Any]] = []
        index: dict[tuple, dict[str, Any]] = {}
        for it in items:
            sess = it.get("session") or {}
            key = (it["document_key"], sess.get("acta_number"), it["pdf_page"] if scoped else None)
            if key in index:
                index[key]["more"] += 1
                continue
            index[key] = {**it, "more": 0}
            grouped.append(index[key])
        for it in grouped[:6]:
            sess = it.get("session") or {}
            when = format_local_date(date.fromisoformat(str(sess["session_date"])[:10])) if sess.get("session_date") else ""
            head = (f"{_video_label({**sess, 'kind': 'sesion'})} · {when}" if sess else f"Gaceta {it['document_key'].split(':')[3]}")
            lines.append(f"• {bold(escape(head))} — p. {it['pdf_page']}")
            lines.append(f"   «{_snippet(it.get('snippet'))}»")
            if it["more"]:
                lines.append(f"   <i>+{it['more']} fragmento(s) más en esta acta</i>")
            if sess.get("url"):
                lines.append(f"   {_acta_link({**sess, 'pdf_page': it['pdf_page']})}")
            if not scoped and sess.get("session_date") and sess.get("corporation") in ("senado", "camara") and len(rows) < 3:
                rows.append([Button(label=f"🏛 {_clip(head, 30)}", intent=Intent.DAY_OVERVIEW,
                                    params={"corporation": sess["corporation"], "date": str(sess["session_date"])[:10]})])
        if not items:
            lines.append(f"No aparece «{escape(query)}» en el texto de {where}. Prueba con otra palabra, sin tildes o "
                         "con menos términos." if data.get("mode") != "vacia" else "Escribe al menos una palabra.")
        lines += ["", "<i>Texto de la Gaceta del Congreso extraído del PDF (puede tener errores de lectura). Cobertura: "
                      "actas cargadas desde 2022, en ampliación; que algo no aparezca no prueba que no se dijo.</i>"]
        rows.append([Button(label="🔎 Otra búsqueda", intent=Intent.ACTA_SEARCH_PROMPT,
                            params={k: params[k] for k in ("document_key", "page_from", "acta_number", "acta_year", "label")
                                    if params.get(k)}), HOME_BUTTON])
        return ViewModel(view_type="acta_search", title=f"Actas · {query}", status="ready" if items else "empty",
                         blocks=["\n".join(lines)], rows=rows)

    def _videos(self, corporation: str | None, start: date, end: date) -> list[dict[str, Any]]:
        """Videos oficiales de sesiones (SRC-26). Si la fuente no responde, la vista sigue sin ellos."""
        try:
            return self.rpc.call("bot_session_videos", {"p_corporation": corporation, "p_from": start.isoformat(),
                                                        "p_to": end.isoformat()}) or []
        except Exception:  # noqa: BLE001 — un enriquecimiento nunca rompe la vista principal
            return []

    def _home(self, ctx: SessionContext) -> ViewModel:
        rows = [[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST),
                 Button(label="🏛 Senado hoy", intent=Intent.DAY_OVERVIEW, params={"corporation": "senado"})],
                [Button(label="🗳 Votaciones", intent=Intent.VOTINGS), Button(label="🗓 Agenda", intent=Intent.AGENDA)],
                [Button(label="💬 Debates", intent=Intent.DISCUSSIONS),
                 Button(label="📄 Buscar en actas", intent=Intent.ACTA_SEARCH_PROMPT)],
                *(self.investigations.home_rows() if self.investigations is not None else []),
                [Button(label="🔗 Fuentes", intent=Intent.SOURCES), HELP_BUTTON]]
        return ViewModel(view_type="home", title="🏛 Tu explorador legislativo", blocks=render_sections([Section(
            "🏛 Tu explorador legislativo", [
                "Consulta proyectos, votaciones, agenda y textos del Congreso, siempre con su fuente.",
                "También puedes escribir: un número (<code>PL 178/2025 Senado</code>), un nombre "
                "(<code>Paloma Valencia</code>) o un tema (<code>proyectos salud</code>).",
            ])]), rows=rows)

    def _from_answer(self, view_type: str, title: str, answer, ctx: SessionContext,
                     rows: list[list[Button]]) -> ViewModel:
        status = {"answered": "ready", "needs_clarification": "partial",
                  "insufficient_evidence": "empty", "failed": "error"}.get(answer.status, "ready")
        return ViewModel(view_type=view_type, title=title, status=status, blocks=render_sections(answer.sections),
                         rows=rows + ([[BACK_BUTTON]] if ctx.current else []))

    def _not_yet(self, intent: Intent) -> ViewModel:
        what = _PENDING_LABELS.get(intent, "esa sección")
        hint = _PENDING_HINT.get(intent, "Mientras tanto puedes escribir tu pregunta.")
        return ViewModel(view_type="unavailable", title="No disponible", status="unavailable",
                         blocks=[f"Todavía no tengo {what}. {hint}"], rows=[[HOME_BUTTON, HELP_BUTTON]])

    # -- proyectos: lista, búsqueda y filtros ----------------------------------------------------

    def _filter_query(self, filters: dict[str, Any], *, search: str | None) -> dict[str, Any]:
        query: dict[str, Any] = {}
        period = filters.get("period", "all" if search else str(RECENT_DAYS))
        days = PERIODS.get(period, PERIODS["all"])[1]
        if days:
            query["since"] = (self.today() - timedelta(days=days)).isoformat()
        for key in ("corporation", "initiative_type"):
            if filters.get(key) and filters[key] != "all":
                query[key] = filters[key]
        if search:
            query["query"] = search
        return query

    def _filters_label(self, filters: dict[str, Any], search: str | None) -> str:
        period = filters.get("period", "all" if search else str(RECENT_DAYS))
        parts = [CORPORATIONS.get(filters.get("corporation", "all"), "Ambas"),
                 PERIODS.get(period, PERIODS["all"])[0]]
        if filters.get("initiative_type", "all") != "all":
            parts.append(TYPES[filters["initiative_type"]])
        return " · ".join(parts)

    def _project_list(self, intent: Intent, params: dict[str, Any], ctx: SessionContext) -> ViewModel:
        if intent is Intent.FILTER_SET:
            key, value = str(params["key"]), str(params["value"])
            if key == "clear":
                search = ctx.applied_filters.get("search")
                ctx.applied_filters = {"search": search} if search else {}
            else:
                ctx.applied_filters[key] = value
        elif intent is Intent.PROJECTS_LIST:
            ctx.applied_filters = {k: v for k, v in ctx.applied_filters.items() if k != "search"}
        else:
            ctx.applied_filters["search"] = str(params.get("query", "")).strip()
        search = ctx.applied_filters.get("search") or None
        data = self.rpc.call("bot_projects_page", {"p_filters": self._filter_query(ctx.applied_filters, search=search),
                                                   "p_limit": 200})
        items = data.get("items") or []
        if search and not items and not {k for k in ctx.applied_filters if k != "search"} and params.get("text"):
            # Sin coincidencias en títulos: se busca por significado en fichas y gacetas (motor).
            answer = self.engine.answer(params["text"])
            view = self._from_answer("search_fallback", "Resultados", answer, ctx, [[HOME_BUTTON]])
            return view
        heading = f"📚 Proyectos · Búsqueda «{escape(search)}»" if search else "📚 Proyectos · Actividad reciente"
        rs = ResultSet(kind="projects", query={"filters": ctx.applied_filters}, title=heading,
                       item_ids=[str(i["project_id"]) for i in items],
                       labels=[f"{i['label']} — {i.get('title') or 'sin título en la fuente'}" for i in items],
                       details=[" · ".join(x for x in (i.get("activity_kind"), fmt_date(i["activity_date"])
                                                       if i.get("activity_date") else None, i.get("status")) if x)
                                for i in items])
        ctx.result_set_id = rs.result_set_id
        view = self._list_page(rs, 1, ctx, total=int(data.get("total") or 0))
        view.new_result_set = rs
        return view

    def _list_page(self, rs: ResultSet | None, page: int, ctx: SessionContext, *, total: int | None = None) -> ViewModel:
        if rs is None:
            return ViewModel(view_type="expired", title="Lista vencida", status="stale", blocks=[
                "Esa lista venció (duran 30 minutos). Te muestro la actividad reciente."],
                rows=[[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST), HOME_BUTTON]])
        page = max(1, min(page, rs.pages()))
        ctx.result_page = page
        filters = rs.query.get("filters", {})
        search = filters.get("search")
        today = self.today()
        period = filters.get("period", "all" if search else str(RECENT_DAYS))
        days = PERIODS.get(period, PERIODS["all"])[1]
        window = f"{fmt_date(today - timedelta(days=days))} – {fmt_date(today)}" if days else "todo el periodo cubierto"
        lines = [bold(rs.title), f"<i>{escape(self._filters_label(filters, search))} · {window}</i>",
                 "<i>Orden: última actividad legislativa registrada (radicación o votación).</i>", ""]
        items = rs.page_items(page)
        if not items:
            lines.append("No encontré proyectos con esos filtros en las fuentes cubiertas.")
            rows = [[Button(label="🗓 Ampliar a 1 año", intent=Intent.FILTER_SET, params={"key": "period", "value": "365"}),
                     Button(label="🧹 Limpiar filtros", intent=Intent.FILTER_SET, params={"key": "clear", "value": ""})],
                    [Button(label="🔎 Buscar", intent=Intent.SEARCH_PROMPT), HOME_BUTTON]]
            return ViewModel(view_type="project_list", title=rs.title, status="empty", blocks=["\n".join(lines)],
                             rows=rows)
        start = (page - 1) * rs.page_size
        rows: list[list[Button]] = []
        for offset, (item_id, label) in enumerate(items):
            n = offset + 1
            detail = rs.details[start + offset] if start + offset < len(rs.details) else ""
            lines.append(f"{n}. {escape(label)}")
            if detail:
                lines.append(f"   <i>{escape(detail)}</i>")
            ref, _, title = label.partition(" — ")
            if not title or title == "sin título en la fuente":
                title = ref.split(" · ", 1)[0]  # sin título: el botón muestra el número
            rows.append([Button(label=f"{n} · {_clip(title, 24)}", intent=Intent.PROJECT_OPEN,
                                params={"project_id": item_id})])
        total_known = total if total is not None else len(rs.item_ids)
        more = f" (se muestran los primeros {len(rs.item_ids)})" if total_known > len(rs.item_ids) else ""
        lines += ["", f"Página {page} de {rs.pages()} · {total_known} proyectos{more}"]
        nav = []
        if page > 1:
            nav.append(Button(label="⬅️ Anterior", intent=Intent.PAGE,
                              params={"result_set_id": rs.result_set_id, "page": page - 1}))
        if page < rs.pages():
            nav.append(Button(label="Siguiente ➡️", intent=Intent.PAGE,
                              params={"result_set_id": rs.result_set_id, "page": page + 1}))
        if nav:
            rows.append(nav)
        rows.append([Button(label="🔎 Buscar", intent=Intent.SEARCH_PROMPT),
                     Button(label="🎛 Filtros", intent=Intent.PROJECTS_FILTERS)])
        rows.append([HOME_BUTTON])
        return ViewModel(view_type="project_list", title=rs.title, blocks=["\n".join(lines)], rows=rows,
                         page=PageInfo(result_set_id=rs.result_set_id, item_ids=[i for i, _ in items], page=page,
                                       has_next=page < rs.pages(), has_previous=page > 1, total=total_known))

    def _filters(self, ctx: SessionContext) -> ViewModel:
        f = ctx.applied_filters
        search = f.get("search")

        def options(key: str, choices: dict[str, str], current: str) -> list[Button]:
            return [Button(label=("✓ " if value == current else "") + label, intent=Intent.FILTER_SET,
                           params={"key": key, "value": value}) for value, label in choices.items()]

        period_default = "all" if search else str(RECENT_DAYS)
        rows = [options("corporation", CORPORATIONS, f.get("corporation", "all")),
                options("period", {k: v[0] for k, v in PERIODS.items()}, f.get("period", period_default)),
                options("initiative_type", TYPES, f.get("initiative_type", "all")),
                [Button(label="🧹 Limpiar", intent=Intent.FILTER_SET, params={"key": "clear", "value": ""}), BACK_BUTTON]]
        return ViewModel(view_type="filters", title="Filtros", blocks=[
            f"{bold('🎛 Filtrar proyectos')}\nCorporación, periodo y tipo. Cada opción se aplica de inmediato."
            + (f"\nBúsqueda activa: «{escape(search)}»" if search else "")], rows=rows)

    def _ordinal(self, position: int, ctx: SessionContext, rs: ResultSet | None) -> ViewModel:
        if rs is None or ctx.result_set_id != rs.result_set_id:
            return ViewModel(view_type="clarify", title="¿De qué lista?", status="partial", blocks=[
                "No tengo una lista vigente para elegir por posición. Abre 📚 Proyectos o busca un tema primero."],
                rows=[[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST), HOME_BUTTON]])
        items = rs.page_items(ctx.result_page)
        if not 1 <= position <= len(items):
            return ViewModel(view_type="clarify", title="Posición fuera de la página", status="partial", blocks=[
                f"La página {ctx.result_page} muestra {len(items)} proyectos; elige un número entre 1 y {len(items)}."],
                rows=[[BACK_BUTTON, HOME_BUTTON]])
        if rs.kind == "votings":
            return self._render(UiAction(intent=Intent.VOTING_OPEN, entry_point="text",
                                         parameters={"voting_id": items[position - 1][0]}), ctx, rs, push=True)
        return self._render(UiAction(intent=Intent.PROJECT_OPEN, entry_point="text",
                                     parameters={"project_id": items[position - 1][0]}), ctx, rs, push=True)

    # -- proyecto abierto ------------------------------------------------------------------------

    def _project_actions(self, project_id: str) -> list[Button]:
        return [Button(label="🗓 Trámite", intent=Intent.PROJECT_TIMELINE, params={"project_id": project_id}),
                Button(label="👥 Autores", intent=Intent.PROJECT_PARTICIPANTS, params={"project_id": project_id}),
                Button(label="🗳 Votaciones", intent=Intent.VOTINGS, params={"project_id": project_id}),
                Button(label="📄 Documentos", intent=Intent.DOCUMENTS, params={"project_id": project_id})]

    def _project_label(self, card: dict[str, Any]) -> str:
        return " · ".join(i["label"] for i in card.get("identifiers") or []) or "Proyecto"

    def _project_card(self, project_id: str, ctx: SessionContext) -> ViewModel:
        card, sections = self.engine.project_card(project_id)
        if not card:
            return ViewModel(view_type="unavailable", title="Proyecto no disponible", status="unavailable",
                             blocks=["Ese proyecto ya no está disponible en las fuentes cargadas."], rows=[[HOME_BUTTON]])
        ctx.active_project_id = project_id
        actions = self._project_actions(project_id)
        rows = [actions[:2], actions[2:]]
        rows.append(([Button(label="⬅️ Resultados", intent=Intent.PAGE,
                             params={"result_set_id": ctx.result_set_id, "page": ctx.result_page})]
                     if ctx.result_set_id else []) + [HOME_BUTTON])
        return ViewModel(view_type="project", title=self._project_label(card), active_project_id=project_id,
                         blocks=render_sections(sections), rows=rows)

    def _sub_rows(self, project_id: str) -> list[list[Button]]:
        return [[Button(label="📌 Ficha", intent=Intent.PROJECT_OPEN, params={"project_id": project_id}), HOME_BUTTON]]

    def _participants(self, project_id: str, ctx: SessionContext) -> ViewModel:
        card = self.rpc.call("bot_project_card", {"p_project_id": project_id}) or {}
        people = self.rpc.call("bot_project_participants", {"p_project_id": project_id}) or []
        ctx.active_project_id = project_id
        label = self._project_label(card)
        lines = [bold(f"👥 Autores · {label}")]
        authors = [p for p in people if p.get("role") == "autor"]
        if authors:
            lines += [f"• {escape(p['name'])}" + (f" <i>({escape(p['party'])})</i>" if p.get("party") else "")
                      for p in authors[:40]]
            if len(authors) > 40:
                lines.append(f"… y {len(authors) - 40} más.")
            sources = sorted({p.get("source") or "" for p in authors} - {""})
            lines += ["", f"<i>Fuente: {escape(', '.join(sources))}. El partido solo está disponible para senadores.</i>"]
        else:
            lines.append("No tengo autores registrados para este proyecto en las fuentes cargadas.")
        lines.append("<i>Ponentes: aún no disponibles (cobertura limitada; están en las ponencias de las gacetas).</i>")
        return ViewModel(view_type="participants", title=f"Autores · {label}", active_project_id=project_id,
                         status="ready" if authors else "empty", blocks=["\n".join(lines)], rows=self._sub_rows(project_id))

    def _documents(self, project_id: str, ctx: SessionContext) -> ViewModel:
        card = self.rpc.call("bot_project_card", {"p_project_id": project_id}) or {}
        ctx.active_project_id = project_id
        label = self._project_label(card)
        docs = self.engine.project_documents(project_id)
        lines = [bold(f"📄 Documentos · {label}")]
        profile = card.get("profile") or {}
        if profile.get("link", "").startswith("https://"):
            lines.append(f"• {link('Ficha en la Cámara', profile['link'])}")
        kinds = {"texto_radicado": "texto radicado", "ponencia": "ponencia", "texto_aprobado": "texto aprobado",
                 "acta": "acta", "concepto": "concepto", "objeciones": "objeciones", "informe": "informe"}
        for d in docs:
            what = ", ".join(sorted(kinds.get(k, k) for k in d["kinds"] if k in kinds)) or "mención"
            pages = sorted(d["pages"])
            where = f"p. {pages[0]}" + (f"–{pages[-1]}" if len(pages) > 1 else "") if pages else ""
            title = d.get("title") or "Gaceta"
            item = link(title, d["url"]) if (d.get("url") or "").startswith("https://") else escape(title)
            lines.append(f"• {item}: {escape(what)}" + (f" · {where}" if where else ""))
        if not docs:
            lines.append("Aún no hay gacetas indexadas con este proyecto; la carga de gacetas desde 2022 está en curso.")
        return ViewModel(view_type="documents", title=f"Documentos · {label}", active_project_id=project_id,
                         status="ready" if docs else "partial", blocks=["\n".join(lines)], rows=self._sub_rows(project_id))

    # -- jornada, agenda, cronología y debates (I3) ------------------------------------------------

    def _resolve_day(self, params: dict[str, Any]) -> tuple[date, str]:
        """(fecha, modo). Una fecha explícita se conserva; una relativa se reinterpreta al actualizar."""
        if params.get("date"):
            return date.fromisoformat(str(params["date"])), "explicit"
        expression = str(params.get("expression") or "hoy")
        now = datetime.combine(self.today(), datetime.min.time(), BOGOTA)
        if (period := resolve_relative_period(expression, now)) is not None:
            return period.start, "relative"
        parsed = parse_spanish_date(expression)
        return (parsed.value or self.today()), "explicit"

    def _day(self, params: dict[str, Any]) -> ViewModel:
        corp = "camara" if params.get("corporation") == "camara" else "senado"
        corp_name = {"senado": "Senado", "camara": "Cámara"}[corp]
        day, mode = self._resolve_day(params)
        data = self.rpc.call("bot_day_overview", {"p_corporation": corp, "p_date": day.isoformat()}) or {}
        future = day > self.today()
        lines = [bold(f"🏛 {corp_name} · {format_local_date(day)}")]
        if mode == "relative" and params.get("expression", "hoy") in ("hoy", "ayer", "manana"):
            lines.append(f"<i>«{escape(str(params.get('expression', 'hoy')))}» en hora de Colombia.</i>")
        status_lines, status_buttons = [], []
        # 1. Confirmado ese día
        lines += ["", bold("Confirmado")]
        if future:
            lines.append("No hay hechos para fechas futuras; abajo está lo programado.")
        else:
            facts = []
            grouped: dict[tuple[str, str], int] = {}
            for v in data.get("votings") or []:
                subject = ", ".join(v.get("projects") or []) or _clip(v.get("subject"), 90)
                key = (subject, f"Sí {v['yes']} · No {v['no']}")
                grouped[key] = grouped.get(key, 0) + 1
            for (subject, tally), n in grouped.items():
                times = f" ({n} votaciones)" if n > 1 else ""
                facts.append(f"• Votación en plenaria: {escape(subject)} — {tally}{times}")
            for f in data.get("filings") or []:
                facts.append(f"• Radicado: {escape(f.get('label') or '')} — {escape(_clip(f.get('title'), 70))}")
            lines += facts or [f"No encontré registros para esta fecha en las fuentes cubiertas."]
            if corp == "camara" and not data.get("votings_covered"):
                lines.append("<i>Las votaciones de la Cámara aún no están en nuestras fuentes.</i>")
        # 1b. Sesiones con video oficial (SRC-26): confirma que hubo sesión y deja ver la fuente primaria.
        rows_search: list[Button] = []
        sessions = _session_groups(self._videos(corp, day, day), self._actas(corp, day, day)) if not future else []
        videos = [g for g in sessions if g.get("urls")]
        if sessions:
            lines += ["", bold("🎥 Sesiones de este día")]
            for g in sessions[:6]:
                topic = f" — {escape(_clip(g['topic'], 140))}" if g.get("topic") else ""
                lines.append(f"• {bold(escape(_video_label(g)))}{topic}")
                links = [link("▶️ video oficial", g["urls"][0][1])] if g.get("urls") else []
                links += [_acta_link(a) for a in g.get("actas", [])[:2]]
                lines.append("   " + " · ".join(links))
            lines.append("<i>El video lo publica el canal oficial; el acta, con la transcripción de la sesión, se "
                         "publica en la Gaceta del Congreso semanas después.</i>")
            searchable = [(g, a) for g in sessions for a in g.get("actas", [])][:2]
            if searchable:
                rows_search = [Button(label=f"🔎 Buscar en acta {a['acta_number']}/{a['acta_year']}", intent=Intent.ACTA_SEARCH_PROMPT,
                                      params={"document_key": a["document_key"], "page_from": a.get("pdf_page"),
                                              "acta_number": a["acta_number"], "acta_year": a["acta_year"],
                                              "label": f"acta {a['acta_number']}/{a['acta_year']} · {_video_label(g)}"})
                               for g, a in searchable]
            else:
                rows_search = []
        # 2. Programado para ese día
        lines += ["", bold("Programado")]
        if not data.get("agenda_covered"):
            lines.append(f"La agenda de la {corp_name if corp == 'camara' else corp_name} no está en nuestras fuentes; "
                         "no significa que no haya sesiones.")
        elif data.get("agenda"):
            lines += [f"• {escape(_clip(a.get('title'), 150))}" for a in data["agenda"][:10]]
            lines.append("<i>Publicar la agenda no confirma que la sesión se celebre.</i>")
        else:
            lines.append(f"Sin agenda publicada para esta fecha (la más reciente es del "
                         f"{fmt_date(data.get('latest_agenda'))}).")
        # 3. Publicado ese día
        docs = self.engine.gacetas_published(corp, day)
        lines += ["", bold("Publicado")]
        if docs:
            for d in docs[:8]:
                title = d.get("title") or "Gaceta"
                lines.append("• " + (link(title, d["url"]) if (d.get("url") or "").startswith("https://") else escape(title)))
        else:
            lines.append("Sin gacetas indexadas con esta fecha (la carga desde 2022 está en curso).")
        coverage = (f"votaciones nominales de plenaria desde 2022 (última: {fmt_date(data.get('latest_voting'))}) y agenda"
                    if corp == "senado" else "radicaciones de la ficha de la Cámara; sin votaciones ni agenda")
        lines += ["", f"<i>Cobertura {corp_name}: {coverage}; gacetas de la Imprenta desde 2022 (carga en curso).</i>"]
        other = "camara" if corp == "senado" else "senado"
        rows = [[Button(label="⬅️ Día anterior", intent=Intent.DAY_OVERVIEW,
                        params={"corporation": corp, "date": (day - timedelta(days=1)).isoformat()}),
                 Button(label="Día siguiente ➡️", intent=Intent.DAY_OVERVIEW,
                        params={"corporation": corp, "date": (day + timedelta(days=1)).isoformat()})],
                [Button(label=f"🏛 {'Cámara' if other == 'camara' else 'Senado'}", intent=Intent.DAY_OVERVIEW,
                        params={"corporation": other, "date": day.isoformat()}),
                 Button(label="🗓 Agenda", intent=Intent.AGENDA,
                        params={"from": day.isoformat(), "to": (day + timedelta(days=6)).isoformat()})],
                [Button(label="🔄 Actualizar", intent=Intent.REFRESH), HOME_BUTTON]]
        if videos:
            rows.insert(0, [Button(label=f"▶️ {_clip(_video_label(v), 30)}", intent=Intent.DAY_OVERVIEW, url=v["urls"][0][1])
                            for v in videos[:2]])
        if sessions and rows_search:
            rows.insert(0, rows_search)
        held_today = bool(data.get("votings") or sessions)
        empty = not (data.get("votings") or data.get("filings") or data.get("agenda") or docs or sessions)
        # «Senado hoy»: el Senado no sesiona todos los días. Si se consulta hoy, o el día no tiene sesión
        # registrada, se dice cuál fue la última y cuál es la próxima publicada (datos reales con su fuente).
        if day == self.today() or not held_today:
            status_lines, status_buttons = self._session_status_block(corp, corp_name, min(day, self.today()))
            if status_lines:
                at = 2 if len(lines) > 1 and "hora de Colombia" in lines[1] else 1  # tras el título y su nota de «hoy»
                lines[at:at] = status_lines
            if status_buttons:
                rows.insert(0, status_buttons)
        return ViewModel(view_type="day", title=f"{corp_name} · {day.isoformat()}", status="empty" if empty else "ready",
                         blocks=["\n".join(lines)], rows=rows)

    def _agenda(self, params: dict[str, Any]) -> ViewModel:
        if params.get("from"):
            start, end = date.fromisoformat(str(params["from"])), date.fromisoformat(str(params["to"]))
        else:
            now = datetime.combine(self.today(), datetime.min.time(), BOGOTA)
            expression = str(params.get("expression") or "esta semana")
            period = resolve_relative_period(expression, now)
            if period is None:
                parsed = parse_spanish_date(expression)
                period = DateRange(parsed.value, parsed.value) if parsed.value else None
            start, end = (period.start, period.end) if period else (self.today(), self.today() + timedelta(days=6))
        data = self.rpc.call("bot_agenda", {"p_from": start.isoformat(), "p_to": end.isoformat(), "p_limit": 25}) or {}
        label = fmt_date(start) if start == end else f"{fmt_date(start)} – {fmt_date(end)}"
        lines = [bold(f"🗓 Agenda · {label}"), "<i>Fuente: Senado · datos abiertos. La agenda de la Cámara no está "
                                               "en nuestras fuentes.</i>", ""]
        items = data.get("items") or []
        if items:
            for item in items:
                lines.append(f"• {fmt_date(item['date'])} · hora no publicada — {escape(_clip(item.get('title'), 150))}")
            if (data.get("total") or 0) > len(items):
                lines.append(f"… y {data['total'] - len(items)} más.")
            lines.append("<i>Publicar la agenda no confirma que la sesión se celebre.</i>")
        else:
            lines.append(f"No encontré agenda publicada para {escape(label)} en las fuentes cubiertas. "
                         f"La más reciente es del {fmt_date(data.get('latest_date'))}.")
        span = (end - start).days + 1
        rows = [[Button(label="⬅️ Anterior", intent=Intent.AGENDA,
                        params={"from": (start - timedelta(days=span)).isoformat(),
                                "to": (start - timedelta(days=1)).isoformat()}),
                 Button(label="Siguiente ➡️", intent=Intent.AGENDA,
                        params={"from": (end + timedelta(days=1)).isoformat(),
                                "to": (end + timedelta(days=span)).isoformat()})],
                [Button(label="🏛 Senado hoy", intent=Intent.DAY_OVERVIEW, params={"corporation": "senado"}), HOME_BUTTON]]
        return ViewModel(view_type="agenda", title=f"Agenda · {label}", status="ready" if items else "empty",
                         blocks=["\n".join(lines)], rows=rows)

    def _timeline(self, project_id: str, ctx: SessionContext) -> ViewModel:
        if not project_id:
            return ViewModel(view_type="clarify", title="¿De qué proyecto?", status="partial",
                             blocks=["¿De qué proyecto? Escribe su número o ábrelo desde 📚 Proyectos."],
                             rows=[[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST), HOME_BUTTON]])
        card = self.rpc.call("bot_project_card", {"p_project_id": project_id}) or {}
        data = self.rpc.call("bot_project_timeline", {"p_project_id": project_id}) or {}
        ctx.active_project_id = project_id
        label = self._project_label(card)
        events = list(data.get("events") or [])
        kinds = {"texto_radicado": "texto radicado", "ponencia": "ponencia", "texto_aprobado": "texto aprobado",
                 "acta": "acta", "concepto": "concepto", "objeciones": "objeciones", "informe": "informe"}
        for d in self.engine.project_documents(project_id, limit=20):
            if d.get("date"):
                what = ", ".join(sorted(kinds[k] for k in d["kinds"] if k in kinds)) or "mención"
                events.append({"date": d["date"], "kind": "publicacion", "detail": f"Publicado en {d['title']}: {what}",
                               "source": "SRC-03", "url": d.get("url")})
        events.sort(key=lambda e: str(e["date"]), reverse=True)
        corp = {"senado": "Senado", "camara": "Cámara"}
        voted = [e for e in events if e.get("kind") == "votacion" and e.get("date")]
        plenary_video: dict[tuple[str, str], str] = {}
        plenary_acta: dict[tuple[str, str], dict[str, Any]] = {}
        if voted:
            dates = [date.fromisoformat(str(e["date"])[:10]) for e in voted]
            for v in self._videos(None, min(dates), max(dates)):
                if v.get("body_key") == "plenaria" and v.get("kind") == "sesion":
                    plenary_video.setdefault((str(v["session_date"])[:10], v.get("corporation")), v["url"])
            for a in self._actas(None, min(dates), max(dates)):
                if a.get("body_key") == "plenaria":
                    plenary_acta.setdefault((str(a["session_date"])[:10], a.get("corporation")), a)
        lines = [bold(f"🗓 Trámite · {label}"), "<i>Hechos fechados en las fuentes, del más reciente al más antiguo. "
                                              "Los del mismo día no tienen orden horario.</i>", ""]
        for e in events[:25]:
            where = f" · {corp[e['corporation']]}" if e.get("corporation") in corp else ""
            detail = escape(e["detail"])
            if e.get("url", "").startswith("https://") if e.get("url") else False:
                detail = link(e["detail"], e["url"])
            key = (str(e["date"])[:10], e.get("corporation"))
            video = plenary_video.get(key) if e.get("kind") == "votacion" else None
            acta = plenary_acta.get(key) if e.get("kind") == "votacion" else None
            watch = (f" · {link('▶️ video de la plenaria', video)}" if video else "") + (f" · {_acta_link(acta)}" if acta else "")
            lines.append(f"• {fmt_date(e['date'])}{where} — {detail} <i>({e['source']})</i>{watch}")
        if not events:
            lines.append("No tengo hechos fechados para este proyecto en las fuentes cargadas.")
        if status := data.get("status"):
            lines += ["", f"Estado reportado: {bold(status['raw'])} — la fuente no fecha el estado; "
                          f"comprobado el {fmt_date(status.get('observed_at'))} ({status['source']})."]
        return ViewModel(view_type="timeline", title=f"Trámite · {label}", active_project_id=project_id,
                         status="ready" if events else "empty", blocks=["\n".join(lines)],
                         rows=self._sub_rows(project_id))

    def _discussions(self, params: dict[str, Any], ctx: SessionContext) -> ViewModel:
        query = params.get("query")
        lines = [bold("🏛 Debates legislativos")]
        if query:
            passages = self.engine.search_actas(str(query))
            lines.append(f"<i>Búsqueda en actas publicadas en gacetas: «{escape(str(query))}»</i>")
            lines += passages or ["No encontré actas sobre eso en las gacetas indexadas."]
            lines.append("<i>Las actas pueden ser parciales; un pasaje no es la transcripción completa de la sesión.</i>")
            status = "ready" if passages else "empty"
        else:
            ctx.pending_clarification = {"expect": "discussion"}
            lines += ["Cobertura limitada: aún no tengo actas ni intervenciones estructuradas por sesión. Puedo "
                      "buscar en las actas publicadas en las Gacetas del Congreso.",
                      "Escribe el tema, por ejemplo <code>debates de salud</code>.",
                      "<i>La conversación pública (prensa y redes) todavía no está habilitada.</i>"]
            status = "partial"
        return ViewModel(view_type="discussions", title="Debates legislativos", status=status,
                         blocks=["\n".join(lines)], rows=[[HOME_BUTTON, HELP_BUTTON]])

    # -- votaciones y evidencia (I4) ----------------------------------------------------------------

    def _votings_list(self, params: dict[str, Any], ctx: SessionContext, *, project_id: str | None) -> ViewModel:
        filters: dict[str, Any] = {}
        heading = "🗳 Votaciones"
        if project_id:
            card = self.rpc.call("bot_project_card", {"p_project_id": project_id}) or {}
            ctx.active_project_id = str(project_id)
            filters["project_id"] = str(project_id)
            heading = f"🗳 Votaciones · {self._project_label(card)}"
        else:
            if not params.get("period"):  # abrir Votaciones empieza sin filtro de persona
                ctx.applied_filters.pop("vote_person_ids", None)
                ctx.applied_filters.pop("vote_person_name", None)
            period = str(params.get("period") or ("30" if not params.get("keep") else
                                                  ctx.applied_filters.get("vote_period", "30")))
            ctx.applied_filters["vote_period"] = period
            days = PERIODS.get(period, PERIODS["30"])[1]
            if days:
                filters["since"] = (self.today() - timedelta(days=days)).isoformat()
            if ctx.applied_filters.get("vote_person_ids"):
                filters["person_ids"] = ctx.applied_filters["vote_person_ids"]
                heading = f"🗳 Votaciones de {ctx.applied_filters.get('vote_person_name', 'la persona')}"
        return self._votings_result(filters, heading, ctx, project_id=project_id)

    def _votings_person(self, params: dict[str, Any], ctx: SessionContext) -> ViewModel:
        if params.get("person_ids"):
            ids = [str(i) for i in params["person_ids"]]
            card = self.rpc.call("bot_person_card", {"p_person_ids": ids}) or {}
            name = card.get("name") or "la persona"
        else:
            from botcentro.domain.names import normalize_name
            tokens = [t for t in normalize_name(str(params.get("name", ""))).split() if len(t) >= 3][:5]
            groups = self.rpc.call("bot_person_search", {"p_tokens": tokens, "p_limit": 5}) if tokens else []
            if not groups:
                return ViewModel(view_type="clarify", title="Persona no encontrada", status="empty", blocks=[
                    f"No encontré a «{escape(str(params.get('name', '')))}» entre los congresistas con datos."],
                    rows=[[Button(label="👤 Otra persona", intent=Intent.VOTE_PERSON_PROMPT), HOME_BUTTON]])
            best = [g for g in groups if g["hits"] == groups[0]["hits"]]
            words = [set(normalize_name(g["name"]).split()) for g in best]
            if len(best) > 1 and not all(w <= max(words, key=len) for w in words):
                return ViewModel(view_type="clarify", title="¿A quién te refieres?", status="partial",
                                 blocks=["¿A quién te refieres?"],
                                 rows=[[Button(label=_clip(g["name"], 28), intent=Intent.VOTINGS_PERSON,
                                               params={"person_ids": g["person_ids"][:6]})] for g in best[:5]])
            ids = [str(pid) for g in best for pid in g["person_ids"]][:6]
            name = max(best, key=lambda g: len(g["name"]))["name"]
        ctx.applied_filters["vote_person_ids"] = ids
        ctx.applied_filters["vote_person_name"] = name
        ctx.applied_filters["vote_period"] = "all"
        return self._votings_result({"person_ids": ids}, f"🗳 Votaciones de {name}", ctx, project_id=None)

    def _votings_result(self, filters: dict[str, Any], heading: str, ctx: SessionContext, *,
                        project_id: str | None) -> ViewModel:
        data = self.rpc.call("bot_votings_page", {"p_filters": filters, "p_limit": 200}) or {}
        items = data.get("items") or []
        vote_label = {"yes": "Votó Sí", "no": "Votó No"}
        rs = ResultSet(kind="votings", query={"filters": filters, "project_id": project_id}, title=heading,
                       item_ids=[str(i["voting_id"]) for i in items],
                       labels=[f"{fmt_date(i['date'])} · {', '.join(i.get('projects') or []) or _clip(i.get('subject'), 60)}"
                               for i in items],
                       details=[" · ".join(x for x in (f"Sí {i['yes']} · No {i['no']}",
                                                       vote_label.get(i.get("person_vote") or "")) if x)
                                for i in items])
        ctx.result_set_id = rs.result_set_id
        view = self._votings_page(rs, 1, ctx, total=int(data.get("total") or 0))
        view.new_result_set = rs
        return view

    def _votings_page(self, rs: ResultSet | None, page: int, ctx: SessionContext, *, total: int | None = None) -> ViewModel:
        if rs is None:
            return self._list_page(None, page, ctx)
        page = max(1, min(page, rs.pages()))
        ctx.result_page = page
        filters = rs.query.get("filters", {})
        project_id = rs.query.get("project_id")
        lines = [bold(rs.title), "<i>Votos nominales de plenaria del Senado desde 2022. Totales calculados a partir "
                                 "del registro nominal; la fuente no publica un resultado oficial agregado.</i>"]
        if not project_id and not filters.get("person_ids"):
            period = ctx.applied_filters.get("vote_period", "30")
            lines.append(f"<i>Periodo: {PERIODS.get(period, PERIODS['30'])[0]}</i>")
        lines.append("")
        items = rs.page_items(page)
        rows: list[list[Button]] = []
        if not items:
            lines.append("No hay votaciones nominales registradas con esos filtros. Las votaciones de la Cámara aún "
                         "no están en nuestras fuentes.")
        start = (page - 1) * rs.page_size
        for offset, (item_id, label) in enumerate(items):
            n = offset + 1
            detail = rs.details[start + offset] if start + offset < len(rs.details) else ""
            lines += [f"{n}. {escape(label)}", f"   <i>{escape(detail)}</i>"]
            short = label.split(" · ", 1)[-1]  # la fecha ya está en el texto
            rows.append([Button(label=f"{n} · {_clip(short, 24)}", intent=Intent.VOTING_OPEN,
                                params={"voting_id": item_id})])
        if items:
            total_known = total if total is not None else len(rs.item_ids)
            lines += ["", f"Página {page} de {rs.pages()} · {total_known} votaciones"]
        nav = []
        if page > 1:
            nav.append(Button(label="⬅️ Anterior", intent=Intent.PAGE,
                              params={"result_set_id": rs.result_set_id, "page": page - 1}))
        if page < rs.pages():
            nav.append(Button(label="Siguiente ➡️", intent=Intent.PAGE,
                              params={"result_set_id": rs.result_set_id, "page": page + 1}))
        if nav:
            rows.append(nav)
        if project_id:
            rows.append([Button(label="📌 Ficha", intent=Intent.PROJECT_OPEN, params={"project_id": project_id}),
                         HOME_BUTTON])
        else:
            rows.append([Button(label=("✓ " if ctx.applied_filters.get("vote_period", "30") == k else "") + v[0],
                                intent=Intent.VOTINGS, params={"period": k}) for k, v in PERIODS.items()][:4])
            person_row = [Button(label="👤 Persona", intent=Intent.VOTE_PERSON_PROMPT)]
            if filters.get("person_ids"):
                person_row.append(Button(label="✖️ Quitar persona", intent=Intent.VOTINGS))
            rows.append(person_row + [HOME_BUTTON])
        return ViewModel(view_type="votings", title=rs.title, status="ready" if items else "empty",
                         blocks=["\n".join(lines)], rows=rows,
                         page=PageInfo(result_set_id=rs.result_set_id, item_ids=[i for i, _ in items], page=page,
                                       has_next=page < rs.pages(), has_previous=page > 1, total=total))

    def _voting_detail(self, voting_id: str, ctx: SessionContext) -> ViewModel:
        d = self.rpc.call("bot_voting_detail", {"p_voting_id": voting_id}) or {}
        if not d:
            return ViewModel(view_type="unavailable", title="Votación no disponible", status="unavailable",
                             blocks=["Esa votación ya no está disponible en las fuentes cargadas."], rows=[[HOME_BUTTON]])
        session = d.get("session") or {}
        organ = {"plenaria": "Plenaria", "comision": f"Comisión {session.get('commission') or ''}".strip()}.get(
            session.get("type") or "", "Sesión")
        corp = {"senado": "del Senado", "camara": "de la Cámara"}.get(session.get("corporation") or "", "")
        projects = d.get("projects") or []
        subject = ", ".join(p["label"] for p in projects) or d.get("subject") or "Asunto sin identificar"
        lines = [bold(f"🗳 {subject}"), f"{organ} {corp} · {fmt_date(d.get('date'))}".strip()]
        if d.get("subject") and projects:
            lines.append(f"<i>Asunto según la fuente: {escape(_clip(d['subject'], 200))}</i>")
        lines += ["", f"Resultado calculado del registro nominal: {bold(f'Sí {d["yes"]} · No {d["no"]}')} "
                      f"({d['yes'] + d['no']} votos registrados)",
                  "Registro nominal: disponible. La fuente solo publica «Sí» y «No»; un senador sin fila no es "
                  "ausencia ni abstención, es falta de dato."]
        if d.get("no_voters"):
            names = d["no_voters"]
            lines.append(f"Votaron No ({len(names)}): " + escape(", ".join(names[:30])) + (" …" if len(names) > 30 else ""))
        evidence = []
        if (d.get("source_url") or "").startswith("https://"):
            lines += ["", f"Fuente: {link('Senado · datos abiertos', d['source_url'])} · capturado el "
                          f"{fmt_date(d.get('fetched_at'))}"]
            evidence.append({"label": f"Senado · datos abiertos (votos {fmt_date(d.get('date'))})",
                             "url": d["source_url"]})
        rows = [[Button(label="📌 Ficha", intent=Intent.PROJECT_OPEN, params={"project_id": p["project_id"]})
                 for p in projects[:2]], [Button(label="⬅️ Votaciones", intent=Intent.BACK), HOME_BUTTON]]
        body_key = ("plenaria" if session.get("type") == "plenaria" else
                    f"comision_{session['commission']}" if str(session.get("commission") or "").isdigit() else None)
        if d.get("date") and body_key:
            day = date.fromisoformat(str(d["date"])[:10])
            match = [v for v in self._videos(session.get("corporation"), day, day)
                     if v.get("body_key") == body_key and v.get("corporation") == session.get("corporation")]
            if match:
                lines += ["", f"▶️ {link('Video oficial de esta sesión', match[0]['url'])} ({escape(match[0]['channel'])})"]
                rows.insert(0, [Button(label="▶️ Ver la sesión", intent=Intent.VOTING_OPEN, url=match[0]["url"])])
                evidence.append({"label": f"Video oficial · {match[0]['title']}", "url": match[0]["url"]})
            actas = [a for a in self._actas(session.get("corporation"), day, day)
                     if a.get("body_key") == body_key and a.get("corporation") == session.get("corporation")]
            if actas:
                lines += ["" if not match else "", f"{_acta_link(actas[0])}: transcripción oficial de la sesión, con el debate y la votación."]
                evidence.append({"label": f"Acta {actas[0]['acta_number']}/{actas[0]['acta_year']} · Gaceta {actas[0]['gaceta']}",
                                 "url": actas[0]["url"]})
        rows = [r for r in rows if r]
        return ViewModel(view_type="voting", title=subject, blocks=["\n".join(lines)], rows=rows, evidence=evidence)

    def _evidence(self, ctx: SessionContext) -> ViewModel:
        if not ctx.last_evidence:
            view = self._from_answer("sources", "Fuentes disponibles", self.engine.answer("/fuentes"), ctx,
                                     [[HOME_BUTTON]])
            view.blocks.insert(0, "<i>No hay una respuesta reciente con fuentes; estas son las fuentes disponibles.</i>")
            return view
        lines = [bold("🔗 Fuentes de esta respuesta"), f"<i>{escape(ctx.last_evidence_title)}</i>", ""]
        for e in ctx.last_evidence:
            url = e.get("url") or ""
            lines.append("• " + (link(e["label"], url) if url.startswith("https://") else escape(e["label"])))
        lines += ["", "<i>Son las fuentes usadas en la última respuesta. «/fuentes» muestra el catálogo completo.</i>"]
        return ViewModel(view_type="evidence", title="Fuentes de esta respuesta", blocks=["\n".join(lines)],
                         rows=[[BACK_BUTTON, Button(label="📚 Catálogo", intent=Intent.SOURCES), HOME_BUTTON]])
