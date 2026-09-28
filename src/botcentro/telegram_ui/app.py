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

from botcentro.domain.dates import BOGOTA
from botcentro.query.engine import AnswerEngine, fmt_date
from botcentro.telegram.render import Section, bold, escape, link, render_sections
from botcentro.telegram_ui.contracts import (Button, Frame, Intent, PageInfo, ResultSet, SessionContext, UiAction,
                                             ViewModel)

HOME_BUTTON = Button(label="🏠 Inicio", intent=Intent.HOME)
HELP_BUTTON = Button(label="❓ Ayuda", intent=Intent.HELP)
BACK_BUTTON = Button(label="⬅️ Volver", intent=Intent.BACK)
IMPLEMENTED = frozenset({
    Intent.HOME, Intent.HELP, Intent.SOURCES, Intent.AGENDA, Intent.BACK, Intent.CANCEL, Intent.REFRESH,
    Intent.QUESTION, Intent.PROJECTS_LIST, Intent.PROJECTS_SEARCH, Intent.PROJECTS_FILTERS, Intent.FILTER_SET,
    Intent.SEARCH_PROMPT, Intent.PAGE, Intent.PROJECT_OPEN, Intent.PROJECT_PARTICIPANTS, Intent.ORDINAL,
    Intent.VOTINGS, Intent.DOCUMENTS,
})
_ENGINE_FALLBACK = frozenset({Intent.PROJECTS_SEARCH})
_PENDING_LABELS = {
    Intent.DAY_OVERVIEW: "la portada del día", Intent.DISCUSSIONS: "los debates legislativos",
    Intent.VOTINGS: "el explorador general de votaciones", Intent.DOCUMENTS: "el explorador general de documentos",
    Intent.COMPARE: "la comparación de versiones", Intent.EVIDENCE: "las fuentes de cada respuesta",
}
_PENDING_HINT = {
    Intent.DAY_OVERVIEW: "Mientras tanto puedes pedir «agenda de esta semana».",
    Intent.VOTINGS: "Abre un proyecto y toca «Votaciones», o pregunta «¿cómo ha votado [nombre]?».",
    Intent.DOCUMENTS: "Abre un proyecto y toca «Documentos».",
}
RECENT_DAYS = 30
PERIODS = {"30": ("30 días", 30), "90": ("90 días", 90), "365": ("1 año", 365), "all": ("Todo", None)}
CORPORATIONS = {"all": "Ambas", "senado": "Senado", "camara": "Cámara"}
TYPES = {"all": "Todos", "proyecto_ley": "Ley", "proyecto_acto_legislativo": "Acto legislativo"}


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class UiApplication:
    def __init__(self, engine: AnswerEngine, *, is_available: Callable[[Intent], bool] | None = None,
                 today: Callable[[], date] | None = None) -> None:
        self.engine = engine
        self.rpc = engine.rpc
        self.is_available = is_available or (lambda intent: intent in IMPLEMENTED)
        self.today = today or (lambda: datetime.now(BOGOTA).date())

    # -- entrada -------------------------------------------------------------------------------

    def handle(self, action: UiAction, ctx: SessionContext, result_set: ResultSet | None = None) -> ViewModel:
        """Aplica la acción sobre el contexto (se modifica en sitio) y devuelve la vista."""
        intent = action.intent
        if intent is Intent.BACK:
            frame = ctx.pop()
            if frame is None:
                return self._home(ctx)
            return self._render(UiAction(intent=frame.intent, entry_point="button", parameters=frame.params), ctx,
                                result_set, push=False)
        if intent is Intent.REFRESH:
            if ctx.current is None:
                return self._home(ctx)
            return self._render(UiAction(intent=ctx.current.intent, entry_point="button",
                                         parameters=ctx.current.params), ctx, result_set, push=False)
        if intent is Intent.CANCEL:
            ctx.pending_clarification = None
            return ViewModel(view_type="cancel", title="Listo", blocks=["Cancelé la selección en curso."],
                             rows=[[HOME_BUTTON, HELP_BUTTON]])
        return self._render(action, ctx, result_set, push=True)

    def _render(self, action: UiAction, ctx: SessionContext, result_set: ResultSet | None, *, push: bool) -> ViewModel:
        intent = action.intent
        params = dict(action.parameters)
        # Captura de búsqueda: tras «🔎 Buscar», un texto breve sin números es el término buscado;
        # cualquier intención explícita distinta la suspende (§19.3).
        if ctx.pending_clarification and ctx.pending_clarification.get("expect") == "search":
            ctx.pending_clarification = None
            text = params.get("text", "")
            if intent is Intent.QUESTION and text and len(text.split()) <= 6 and not any(c.isdigit() for c in text):
                intent, params = Intent.PROJECTS_SEARCH, {"query": text, "text": text}
        if not self.is_available(intent) and intent in _ENGINE_FALLBACK and params.get("text"):
            intent, params = Intent.QUESTION, {"text": params["text"]}
        if not self.is_available(intent):
            return self._not_yet(intent)

        if intent is Intent.HOME:
            ctx.navigation_stack = []
            ctx.applied_filters = {}
            view = self._home(ctx)
        elif intent is Intent.HELP:
            view = self._from_answer("help", "Ayuda", self.engine.answer("/help"), ctx, [[HOME_BUTTON]])
        elif intent is Intent.SOURCES:
            view = self._from_answer("sources", "Fuentes disponibles", self.engine.answer("/fuentes"), ctx,
                                     [[HOME_BUTTON]])
        elif intent is Intent.AGENDA:
            text = params.get("text") or "agenda de esta semana"
            view = self._from_answer("agenda", "Agenda", self.engine.answer(text), ctx, [[HOME_BUTTON]])
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
            view = self._list_page(result_set, int(params.get("page", 1)), ctx)
            push = False
        elif intent is Intent.ORDINAL:
            return self._ordinal(int(params.get("position", 0)), ctx, result_set)
        elif intent is Intent.PROJECT_OPEN:
            view = self._project_card(str(params["project_id"]), ctx)
        elif intent in (Intent.PROJECT_PARTICIPANTS, Intent.VOTINGS, Intent.DOCUMENTS):
            project_id = params.get("project_id") or (ctx.active_project_id if params.get("contextual")
                                                      or action.entry_point == "text" else None)
            if not project_id:
                if intent is Intent.PROJECT_PARTICIPANTS:
                    return ViewModel(view_type="clarify", title="¿De qué proyecto?", status="partial", blocks=[
                        "¿De qué proyecto? Escribe su número (<code>PL 178/2025 Senado</code>) o abre uno desde "
                        "📚 Proyectos."], rows=[[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST), HOME_BUTTON]])
                return self._not_yet(intent)
            params["project_id"] = project_id
            view = {Intent.PROJECT_PARTICIPANTS: self._participants, Intent.VOTINGS: self._votings,
                    Intent.DOCUMENTS: self._documents}[intent](str(project_id), ctx)
        else:  # pregunta libre: el motor sin IA resuelve proyecto, persona, agenda o búsqueda
            answer = self.engine.answer(params.get("text", ""))
            rows: list[list[Button]] = []
            if len(answer.project_ids) == 1:
                ctx.active_project_id = answer.project_ids[0]
                rows.append(self._project_actions(answer.project_ids[0]))
            view = self._from_answer("answer", "Respuesta", answer, ctx, rows + [[HOME_BUTTON, HELP_BUTTON]])
            view.active_project_id = ctx.active_project_id if answer.project_ids else None
        if push:
            ctx.push(Frame(intent=intent, params=params, title=view.title))
        return view

    # -- inicio y ayudas -----------------------------------------------------------------------

    def _home(self, ctx: SessionContext) -> ViewModel:
        rows = [[Button(label="📚 Proyectos", intent=Intent.PROJECTS_LIST), Button(label="🗓 Agenda", intent=Intent.AGENDA)],
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
        return self._render(UiAction(intent=Intent.PROJECT_OPEN, entry_point="text",
                                     parameters={"project_id": items[position - 1][0]}), ctx, rs, push=True)

    # -- proyecto abierto ------------------------------------------------------------------------

    def _project_actions(self, project_id: str) -> list[Button]:
        return [Button(label="👥 Autores", intent=Intent.PROJECT_PARTICIPANTS, params={"project_id": project_id}),
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
        rows = [self._project_actions(project_id)]
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

    def _votings(self, project_id: str, ctx: SessionContext) -> ViewModel:
        card, sections = self.engine.project_card(project_id)
        ctx.active_project_id = project_id
        label = self._project_label(card)
        votes = [s for s in sections if s.title == "Votaciones"]
        blocks = [bold(f"🗳 Votaciones · {label}")]
        if votes:
            blocks += render_sections(votes)
            status = "ready"
        else:
            blocks.append("No hay votaciones nominales registradas para este proyecto. El Senado publica votos "
                          "nominales de plenaria desde 2022; las votaciones de la Cámara aún no están en nuestras fuentes.")
            status = "empty"
        return ViewModel(view_type="votings", title=f"Votaciones · {label}", status=status, active_project_id=project_id,
                         blocks=["\n".join(blocks)], rows=self._sub_rows(project_id))

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
