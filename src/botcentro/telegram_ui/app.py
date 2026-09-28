"""Casos de uso de la interfaz: acción + contexto → vista (centrorequirement.md §4, §6, §7, §19).

La capa es síncrona y sin efectos de negocio al renderizar: consulta servicios de dominio
(`AnswerEngine` y lecturas `bot_*`) y devuelve un `ViewModel` con las acciones válidas. Las
capacidades aún no implementadas no aparecen en los menús; si se piden por texto, se explica
el límite y se ofrece una alternativa (UI-F05, UI-F39).
"""

from __future__ import annotations

from collections.abc import Callable

from botcentro.query.engine import AnswerEngine
from botcentro.telegram.render import Section, render_sections
from botcentro.telegram_ui.contracts import Button, Frame, Intent, SessionContext, UiAction, ViewModel

HOME_BUTTON = Button(label="🏠 Inicio", intent=Intent.HOME)
HELP_BUTTON = Button(label="❓ Ayuda", intent=Intent.HELP)
BACK_BUTTON = Button(label="⬅️ Volver", intent=Intent.BACK)
# Etapa I1: lo que ya responde con datos reales. Las demás secciones llegan en I2–I4.
IMPLEMENTED = frozenset({Intent.HOME, Intent.HELP, Intent.SOURCES, Intent.AGENDA, Intent.BACK, Intent.CANCEL,
                         Intent.REFRESH, Intent.QUESTION})
_ENGINE_FALLBACK = frozenset({Intent.PROJECTS_SEARCH})
_PENDING_LABELS = {
    Intent.PROJECTS_LIST: "el explorador de proyectos", Intent.PROJECTS_SEARCH: "la búsqueda con filtros",
    Intent.DAY_OVERVIEW: "la portada del día", Intent.DISCUSSIONS: "los debates legislativos",
    Intent.VOTINGS: "el explorador de votaciones", Intent.DOCUMENTS: "la lista de documentos",
    Intent.PROJECT_PARTICIPANTS: "las preguntas sobre el proyecto abierto", Intent.ORDINAL: "la selección por posición",
    Intent.COMPARE: "la comparación de versiones", Intent.EVIDENCE: "las fuentes de cada respuesta",
}
_PENDING_HINT = {
    Intent.PROJECTS_LIST: "Mientras tanto escribe un tema («proyectos sobre agua») o un número («PL 178/2025 Senado»).",
    Intent.DAY_OVERVIEW: "Mientras tanto puedes pedir «agenda de esta semana».",
    Intent.VOTINGS: "Mientras tanto pregunta «¿cómo ha votado [nombre]?» o escribe el número de un proyecto.",
    Intent.PROJECT_PARTICIPANTS: "Mientras tanto escribe el número del proyecto: su ficha incluye los autores.",
}


class UiApplication:
    def __init__(self, engine: AnswerEngine, *, is_available: Callable[[Intent], bool] | None = None) -> None:
        self.engine = engine
        self.is_available = is_available or (lambda intent: intent in IMPLEMENTED)

    def handle(self, action: UiAction, ctx: SessionContext) -> ViewModel:
        """Aplica la acción sobre el contexto (se modifica en sitio) y devuelve la vista."""
        intent = action.intent
        if intent is Intent.BACK:
            frame = ctx.pop()
            if frame is None:
                return self._home(ctx)
            return self._render(UiAction(intent=frame.intent, entry_point="button", parameters=frame.params), ctx,
                                push=False)
        if intent is Intent.REFRESH:
            if ctx.current is None:
                return self._home(ctx)
            return self._render(UiAction(intent=ctx.current.intent, entry_point="button",
                                         parameters=ctx.current.params), ctx, push=False)
        if intent is Intent.CANCEL:
            ctx.pending_clarification = None
            return ViewModel(view_type="cancel", title="Listo", blocks=["Cancelé la selección en curso."],
                             rows=[[HOME_BUTTON, HELP_BUTTON]])
        return self._render(action, ctx, push=True)

    def _render(self, action: UiAction, ctx: SessionContext, *, push: bool) -> ViewModel:
        intent = action.intent
        if not self.is_available(intent) and intent in _ENGINE_FALLBACK and action.parameters.get("text"):
            # El motor de preguntas cubre este caso mientras la sección dedicada no exista: sin bucles.
            action = UiAction(intent=Intent.QUESTION, entry_point=action.entry_point,
                              parameters={"text": action.parameters["text"]})
            intent = action.intent
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
            text = action.parameters.get("text") or "agenda de esta semana"
            view = self._from_answer("agenda", "Agenda", self.engine.answer(text), ctx, [[HOME_BUTTON]])
        else:  # pregunta libre: el motor sin IA resuelve proyecto, persona, agenda o búsqueda
            answer = self.engine.answer(action.parameters.get("text", ""))
            view = self._from_answer("answer", "Respuesta", answer, ctx, [[HOME_BUTTON, HELP_BUTTON]])
            if answer.project_ids:
                ctx.active_project_id = answer.project_ids[0]  # contexto breve para preguntas de seguimiento
                view.active_project_id = answer.project_ids[0]
        if push:
            ctx.push(Frame(intent=intent, params=dict(action.parameters), title=view.title))
        return view

    def _from_answer(self, view_type: str, title: str, answer, ctx: SessionContext,
                     rows: list[list[Button]]) -> ViewModel:
        status = {"answered": "ready", "needs_clarification": "partial",
                  "insufficient_evidence": "empty", "failed": "error"}.get(answer.status, "ready")
        return ViewModel(view_type=view_type, title=title, status=status, blocks=render_sections(answer.sections),
                         rows=rows + ([[BACK_BUTTON]] if ctx.navigation_stack or ctx.current else []))

    def _home(self, ctx: SessionContext) -> ViewModel:
        rows = [[Button(label="🗓 Agenda", intent=Intent.AGENDA), Button(label="🔗 Fuentes", intent=Intent.SOURCES)],
                [HELP_BUTTON]]
        return ViewModel(view_type="home", title="🏛 Tu explorador legislativo", blocks=render_sections([Section(
            "🏛 Tu explorador legislativo", [
                "Consulta proyectos, votaciones, agenda y textos del Congreso, siempre con su fuente.",
                "Escribe un número (<code>PL 178/2025 Senado</code>), un nombre (<code>Paloma Valencia</code>) "
                "o un tema (<code>proyectos sobre agua</code>).",
            ])]), rows=rows)

    def _not_yet(self, intent: Intent) -> ViewModel:
        what = _PENDING_LABELS.get(intent, "esa sección")
        hint = _PENDING_HINT.get(intent, "Mientras tanto puedes escribir tu pregunta.")
        return ViewModel(view_type="unavailable", title="No disponible", status="unavailable",
                         blocks=[f"Todavía no tengo {what}. {hint}"], rows=[[HOME_BUTTON, HELP_BUTTON]])
