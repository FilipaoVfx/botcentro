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
                                                                                     "vote_person", "territory"):
            expect = ctx.pending_clarification["expect"]
            ctx.pending_clarification = None
            text = params.get("text", "")
            if intent is Intent.QUESTION and text and len(text.split()) <= 6 and not any(c.isdigit() for c in text):
                intent, params = {"search": (Intent.PROJECTS_SEARCH, {"query": text, "text": text}),
                                  "discussion": (Intent.DISCUSSIONS, {"query": text, "text": text}),
                                  "vote_person": (Intent.VOTINGS_PERSON, {"name": text}),
                                  "territory": (Intent.TERRITORY_RESOLVE, {"name": text, "text": text})}[expect]
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

    def _home(self, ctx: SessionContext) -> ViewModel:
        rows = [[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST),
                 Button(label="🏛 Senado hoy", intent=Intent.DAY_OVERVIEW, params={"corporation": "senado"})],
                [Button(label="🗳 Votaciones", intent=Intent.VOTINGS), Button(label="🗓 Agenda", intent=Intent.AGENDA)],
                [Button(label="💬 Debates", intent=Intent.DISCUSSIONS)],
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
        empty = not (data.get("votings") or data.get("filings") or data.get("agenda") or docs)
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
        lines = [bold(f"🗓 Trámite · {label}"), "<i>Hechos fechados en las fuentes, del más reciente al más antiguo. "
                                              "Los del mismo día no tienen orden horario.</i>", ""]
        for e in events[:25]:
            where = f" · {corp[e['corporation']]}" if e.get("corporation") in corp else ""
            detail = escape(e["detail"])
            if e.get("url", "").startswith("https://") if e.get("url") else False:
                detail = link(e["detail"], e["url"])
            lines.append(f"• {fmt_date(e['date'])}{where} — {detail} <i>({e['source']})</i>")
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
