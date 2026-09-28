"""Contratos de la interfaz conversacional (centrorequirement.md §21; DEC-18).

Toda entrada (texto, comando o botón) se normaliza a una `UiAction`; cada respuesta es un
`ViewModel` con las acciones válidas calculadas por el servidor. El contexto de sesión es breve
(≤ 24 h) y versionado: `revision` permite detectar cambios concurrentes (compare-and-swap).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class Intent(StrEnum):
    HOME = "home.open"
    HELP = "help.open"
    PROJECTS_LIST = "projects.list"
    PROJECTS_SEARCH = "projects.search"
    PROJECTS_FILTERS = "projects.filters"
    FILTER_SET = "projects.filter_set"
    SEARCH_PROMPT = "projects.search_prompt"
    PROJECT_RESOLVE = "projects.resolve"
    PROJECT_OPEN = "project.open"
    PROJECT_PARTICIPANTS = "project.participants"
    PROJECT_TIMELINE = "project.timeline"
    DAY_OVERVIEW = "day.overview"
    AGENDA = "agenda.list"
    DISCUSSIONS = "discussions.open"
    VOTINGS = "votings.list"
    VOTINGS_PERSON = "votings.person"
    VOTING_OPEN = "voting.open"
    VOTE_PERSON_PROMPT = "votings.person_prompt"
    DOCUMENTS = "documents.list"
    COMPARE = "versions.compare"
    EVIDENCE = "evidence.show"
    SOURCES = "sources.catalog"
    PAGE = "page.goto"
    ORDINAL = "selection.ordinal"
    BACK = "navigation.back"
    CANCEL = "interaction.cancel"
    REFRESH = "view.refresh"
    QUESTION = "question.free"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UiAction(_Model):
    """Intención normalizada. Principal, chat y ámbito nunca vienen aquí: salen del update."""

    schema_version: str = SCHEMA_VERSION
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    intent: Intent
    entry_point: Literal["text", "command", "button"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    parameter_origins: dict[str, Literal["explicit", "contextual", "default"]] = Field(default_factory=dict)
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Button(_Model):
    label: str
    intent: Intent
    params: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None  # enlace externo del catálogo autorizado (no genera callback)


class PageInfo(_Model):
    result_set_id: str
    item_ids: list[str]
    page: int = 1
    has_next: bool = False
    has_previous: bool = False
    total: int | None = None  # None: desconocido, nunca 0 por defecto


class ResultSet(_Model):
    """Lista mostrada: los ordinales («el segundo») se resuelven contra estos IDs, nunca contra
    una búsqueda nueva (UI-F09)."""

    result_set_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: Literal["projects", "persons", "votings", "documents"]
    query: dict[str, Any] = Field(default_factory=dict)
    item_ids: list[str]
    labels: list[str]
    details: list[str] = Field(default_factory=list)
    title: str = ""
    page_size: int = 5
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def page_items(self, page: int) -> list[tuple[str, str]]:
        start = (page - 1) * self.page_size
        return list(zip(self.item_ids, self.labels))[start:start + self.page_size]

    def pages(self) -> int:
        return max(1, -(-len(self.item_ids) // self.page_size))


ViewStatus = Literal["ready", "empty", "partial", "stale", "unavailable", "error", "loading"]


class ViewModel(_Model):
    view_type: str
    title: str
    status: ViewStatus = "ready"
    blocks: list[str] = Field(default_factory=list)  # HTML ya escapado por los presentadores
    rows: list[list[Button]] = Field(default_factory=list)
    page: PageInfo | None = None
    active_project_id: str | None = None
    data_as_of: datetime | None = None
    context_label: str | None = None
    new_result_set: ResultSet | None = None  # lista a guardar en Redis (no se muestra)
    evidence: list[dict[str, str]] = Field(default_factory=list)  # fuentes usadas en esta vista


class Frame(_Model):
    """Marco de retorno: lo necesario para reconstruir una vista anterior."""

    intent: Intent
    params: dict[str, Any] = Field(default_factory=dict)
    title: str = ""


class SessionContext(_Model):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = 1
    revision: int = 0
    current: Frame | None = None
    anchor_message_id: int | None = None
    active_project_id: str | None = None
    active_person_ids: list[str] = Field(default_factory=list)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    result_set_id: str | None = None
    result_page: int = 1
    navigation_stack: list[Frame] = Field(default_factory=list)
    pending_clarification: dict[str, Any] | None = None
    last_evidence: list[dict[str, str]] = Field(default_factory=list)
    last_evidence_title: str = ""
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=24))

    def push(self, frame: Frame, *, depth: int = 3) -> None:
        if self.current is not None and self.current != frame:
            self.navigation_stack = (self.navigation_stack + [self.current])[-depth:]
        self.current = frame

    def pop(self) -> Frame | None:
        if not self.navigation_stack:
            return None
        self.current = self.navigation_stack.pop()
        return self.current


class CallbackRecord(_Model):
    """Lo que un botón significa, guardado en el servidor; el botón solo lleva un token opaco."""

    owner_user_id: int
    chat_id: int
    intent: Intent
    params: dict[str, Any] = Field(default_factory=dict)
    session_id: str
    view_revision: int
