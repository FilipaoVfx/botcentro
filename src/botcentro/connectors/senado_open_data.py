"""Conector SRC-01: API pública «Datos Públicos» del Senado (docs/fuentes/SRC-01.md).

API: https://app.senado.gov.co/backend/api/public/v1/{dataset}?format=json[&start_at&end_at]

Descubrimiento: los catálogos (senadores, comisiones) una vez por ejecución y los conjuntos
fechados (agenda, votos, asistencias) por ventanas de `window_days` dentro del alcance. Sin
rango, la API devolvería todo el histórico: nunca se pide sin fechas.

Semántica validada con muestras reales (2026-09):
  * votos: solo «Si»/«No»; una fila por senador, plenaria y proyecto. El acto de votación se
    identifica por (plenary_id, project_id). `project_name` trae la numeración oficial, a veces
    doble Senado–Cámara, que es evidencia explícita de vínculo (T-06);
  * asistencias: «Si»/«No» explícitos ⇒ present/absent;
  * agenda: programación publicada; no prueba que la sesión se celebró;
  * senadores: se descartan teléfonos, correos y redes (minimización, SRS-N05).
Los sondeos de la app (`surveys`) no son votaciones oficiales y no se ingieren.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

from botcentro.connectors.base import (
    Cursor,
    DiscoveredItem,
    DiscoverPage,
    Issue,
    IssueSeverity,
    NormalizedCandidate,
    ParseResult,
    ValidationReport,
)
from botcentro.domain.dates import BOGOTA, DateParseError, PartialDate, parse_spanish_date
from botcentro.domain.names import display_name, normalize_name
from botcentro.domain.project_ids import format_ref, parse_project_citations
from botcentro.domain.votes import AttendanceStatus, VoteMapping, VoteValue, normalize_attendance
from botcentro.domain.hashing import sha256_hex
from botcentro.errors import FetchError
from botcentro.http.fetcher import Fetched, NotModified, SafeFetcher

API = "https://app.senado.gov.co/backend/api/public/v1"
ALLOWED_DOMAINS = ("app.senado.gov.co",)
CATALOGS = ("senators", "commissions")
DATED = ("events", "votes", "assistances")
AGENDA_AHEAD_DAYS = 21
JSON_ONLY = frozenset({"application/json"})
REF = "senado-od"
EMPTY_RANGE_RE = re.compile(r"No existen .* en el rango de fechas", re.IGNORECASE)

# Campos esperados por conjunto: su ausencia es un cambio de esquema (cuarentena + caso).
EXPECTED: Mapping[str, frozenset[str]] = {
    "senators": frozenset({"id", "name", "party_name", "commission_id"}),
    "commissions": frozenset({"id", "name", "description"}),
    "events": frozenset({"id", "title", "date", "time"}),
    "votes": frozenset({"plenary_id", "created_at", "senator_id", "senator_name", "project_id", "project_name", "vote"}),
    "assistances": frozenset({"plenary_id", "plenary_created_at", "senator_id", "senator", "attended"}),
}

# «Si»/«No» son las únicas etiquetas observadas; cualquier otra queda en cuarentena.
VOTES = VoteMapping({"si": VoteValue.YES, "no": VoteValue.NO})
ATTENDANCE = {"si": AttendanceStatus.PRESENT, "no": AttendanceStatus.ABSENT}


def _day(value: str) -> PartialDate:
    return parse_spanish_date(value)


class SenadoOpenDataConnector:
    adapter = "senado_open_data"
    version = "0.1.0"
    parser_version = "senado-od-parser-2"

    def __init__(self, fetcher: SafeFetcher, *, today: Callable[[], date] | None = None) -> None:
        self._fetcher = fetcher
        self._today = today or (lambda: datetime.now(BOGOTA).date())

    # -- §7.1 validate_source ------------------------------------------------------------------
    def validate_source(self, config: Mapping[str, Any]) -> ValidationReport:
        problems = []
        if not config.get("from") or not config.get("to"):
            problems.append("el alcance necesita fechas from/to: sin rango la API devuelve todo el histórico")
        return ValidationReport(capabilities=[*CATALOGS, *DATED], diagnostics=problems)

    # -- §7.1 discover -------------------------------------------------------------------------
    def discover(self, cursor: Cursor) -> DiscoverPage:
        """Una página = catálogos (primera) o una ventana de fechas con sus tres conjuntos."""
        scope = cursor.scope
        # Votos y asistencias: la API rechaza (HTTP 400) cualquier fecha futura, así que el alcance se recorta a
        # hoy en Bogotá. La agenda (`events`) sí acepta un final futuro si el inicio es anterior a hoy
        # (verificado 2026-10-06): la última ventana pide además AGENDA_AHEAD_DAYS hacia adelante, que es lo
        # que permite mostrar la próxima sesión programada.
        start = date.fromisoformat(scope["from"])
        end = min(date.fromisoformat(scope["to"]), self._today())
        window = timedelta(days=int(scope.get("window_days", 7)))
        page = int(cursor.position.get("page", 0))

        if page == 0:
            items = [DiscoveredItem(record_type=name, logical_key=name, url=f"{API}/{name}?format=json",
                                    accept_mimes=JSON_ONLY) for name in CATALOGS]
            has_more = start <= end
            return DiscoverPage(items, cursor.advance(page=1), has_more)

        window_start = start + window * (page - 1)
        window_end = min(window_start + window - timedelta(days=1), end)
        # La API exige start_at anterior a hoy (400 «debe ser una fecha anterior a …»): una ventana que
        # empieza hoy se adelanta un día; el solape se deduplica por clave de observación.
        request_start = min(window_start, self._today() - timedelta(days=1))
        ahead = window_end >= self._today()
        items = []
        for name in DATED:
            item_end = window_end + timedelta(days=AGENDA_AHEAD_DAYS) if (name == "events" and ahead) else window_end
            items.append(DiscoveredItem(
                record_type=name,
                logical_key=f"{name}:{request_start.isoformat()}:{item_end.isoformat()}",
                url=f"{API}/{name}?format=json&start_at={request_start.isoformat()}&end_at={item_end.isoformat()}",
                accept_mimes=JSON_ONLY,
                hints={"from": request_start.isoformat(), "to": item_end.isoformat()},
            ))
        return DiscoverPage(items, cursor.advance(page=page + 1), has_more=window_end < end)

    # -- §7.1 fetch ----------------------------------------------------------------------------
    def fetch(self, item: DiscoveredItem, *, etag: str | None = None,
              last_modified: str | None = None) -> Fetched | NotModified:
        try:
            return self._fetcher.fetch(item.url, etag=etag, last_modified=last_modified, accept_mimes=item.accept_mimes)
        except FetchError as exc:
            # La API responde 400 {"error": "No existen … en el rango de fechas …"} a un rango sin
            # datos: es un vacío válido. Se conserva la respuesta real como captura (evidencia).
            if exc.status == 400 and exc.body and EMPTY_RANGE_RE.search(exc.body.decode("utf-8", "replace")):
                return Fetched(item.url, item.url, 400, exc.body, sha256_hex(exc.body), "application/json",
                               "application/json", None, None, datetime.now(timezone.utc))
            raise

    # -- §7.1 parse ----------------------------------------------------------------------------
    def parse(self, item: DiscoveredItem, snapshot: Fetched) -> ParseResult:
        try:
            rows = json.loads(snapshot.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return ParseResult(issues=[Issue("INVALID_JSON", str(exc), IssueSeverity.SCHEMA_CHANGE)])
        if not rows or (isinstance(rows, dict) and EMPTY_RANGE_RE.search(str(rows.get("error", "")))):
            return ParseResult(empty_source=True)
        if not isinstance(rows, list):
            return ParseResult(issues=[Issue("NOT_A_LIST", "se esperaba una lista de registros", IssueSeverity.SCHEMA_CHANGE)])

        expected = EXPECTED[item.record_type]
        issues: list[Issue] = []
        candidates: list[NormalizedCandidate] = []
        handler = getattr(self, f"_parse_{item.record_type}")
        for index, row in enumerate(rows):
            pointer = f"/{index}"
            missing = expected - set(row) if isinstance(row, dict) else expected
            if missing:
                issues.append(Issue("MISSING_FIELDS", f"faltan {sorted(missing)} en {pointer}",
                                    IssueSeverity.SCHEMA_CHANGE, pointer))
                continue
            try:
                candidates.extend(handler(row, pointer))
            except (DateParseError, ValueError) as exc:
                issues.append(Issue("UNPARSEABLE_ROW", str(exc), IssueSeverity.QUARANTINE, pointer))
        # El acto de votación se repite en cada fila nominal: una sola observación por captura.
        unique: dict[tuple[str, str, str], NormalizedCandidate] = {}
        for candidate in candidates:
            key = (candidate.subject_ref, candidate.predicate, json.dumps(candidate.value, sort_keys=True, ensure_ascii=False))
            unique.setdefault(key, candidate)
        return ParseResult(candidates=list(unique.values()), issues=issues)

    def _parse_senators(self, row: Mapping[str, Any], pointer: str) -> list[NormalizedCandidate]:
        name = display_name(str(row["name"]))
        return [NormalizedCandidate(
            subject_type="person", subject_ref=f"{REF}:senator:{row['id']}", predicate="senator_profile",
            value={"name": name, "normalized_name": normalize_name(name), "party": row.get("party_name"),
                   "commission_ref": f"{REF}:commission:{row['commission_id']}" if row.get("commission_id") else None},
            value_raw=str(row["name"]), record_pointer=pointer,
        )]

    def _parse_commissions(self, row: Mapping[str, Any], pointer: str) -> list[NormalizedCandidate]:
        description = str(row.get("description") or "")
        competences = re.split(r"\n\s*Mesa Directiva", description, maxsplit=1)[0].strip()
        return [NormalizedCandidate(
            subject_type="commission", subject_ref=f"{REF}:commission:{row['id']}", predicate="commission_profile",
            value={"name": display_name(str(row["name"])), "competences": competences[:4000]},
            record_pointer=pointer,
        )]

    def _parse_events(self, row: Mapping[str, Any], pointer: str) -> list[NormalizedCandidate]:
        day = _day(str(row["date"]))
        at = None
        if day.value and re.fullmatch(r"\d{1,2}:\d{2}", str(row.get("time") or "")):
            hour, minute = map(int, str(row["time"]).split(":"))
            at = datetime(day.value.year, day.value.month, day.value.day, hour, minute, tzinfo=BOGOTA)
        return [NormalizedCandidate(
            subject_type="agenda_item", subject_ref=f"{REF}:event:{row['id']}", predicate="scheduled",
            value={"title": str(row["title"]).strip(), "status": "scheduled", "link": row.get("link"),
                   "local_date": day.iso(), "time": row.get("time")},
            value_raw=str(row["title"]), effective=day, effective_at=at, record_pointer=pointer,
        )]

    def _parse_votes(self, row: Mapping[str, Any], pointer: str) -> list[NormalizedCandidate]:
        day = _day(str(row["created_at"]))
        voting_ref = f"{REF}:voting:{row['plenary_id']}:{row['project_id']}"
        normalized = VOTES.normalize(str(row["vote"]))
        citations = parse_project_citations(str(row["project_name"]))
        refs = [format_ref(r) for c in citations for r in c.refs]
        voting = NormalizedCandidate(
            subject_type="voting", subject_ref=voting_ref, predicate="voting_subject",
            value={"plenary_ref": f"{REF}:plenary:{row['plenary_id']}", "subject_text": str(row["project_name"]).strip(),
                   "subject_type": "project" if refs else "other", "project_refs": refs,
                   "explicit_link": any(c.explicit_link for c in citations), "local_date": day.iso()},
            value_raw=str(row["project_name"]), effective=day, record_pointer=pointer,
        )
        vote = NormalizedCandidate(
            subject_type="vote", subject_ref=f"{voting_ref}:{row['senator_id']}", predicate="nominal_vote",
            value={"voting_ref": voting_ref, "person_ref": f"{REF}:senator:{row['senator_id']}",
                   "vote_raw": str(row["vote"]), "vote": normalized.value},
            value_raw=str(row["vote"]), effective=day, record_pointer=pointer,
            quarantine_reason=None if normalized in (VoteValue.YES, VoteValue.NO)
            else f"etiqueta de voto no validada: {row['vote']!r}",
        )
        return [voting, vote, self._seen(row["senator_id"], row["senator_name"], day, pointer)]

    def _seen(self, senator_id: Any, raw_name: Any, day: PartialDate, pointer: str) -> NormalizedCandidate:
        """Nombre del senador tal como aparece en votos/asistencias: identifica a senadores
        históricos que ya no están en el catálogo actual."""
        name = display_name(str(raw_name))
        return NormalizedCandidate(
            subject_type="person", subject_ref=f"{REF}:senator:{senator_id}", predicate="senator_seen",
            value={"name": name, "normalized_name": normalize_name(name)}, value_raw=str(raw_name),
            record_pointer=pointer,
        )

    def _parse_assistances(self, row: Mapping[str, Any], pointer: str) -> list[NormalizedCandidate]:
        day = _day(str(row["plenary_created_at"]))
        status = normalize_attendance(str(row["attended"]), ATTENDANCE)
        seen = self._seen(row["senator_id"], row["senator"], day, pointer)
        return [seen, NormalizedCandidate(
            subject_type="attendance", subject_ref=f"{REF}:attendance:{row['plenary_id']}:{row['senator_id']}",
            predicate="attendance",
            value={"plenary_ref": f"{REF}:plenary:{row['plenary_id']}", "person_ref": f"{REF}:senator:{row['senator_id']}",
                   "status_raw": str(row["attended"]), "status": status.value},
            value_raw=str(row["attended"]), effective=day, record_pointer=pointer,
            quarantine_reason=None if status is not AttendanceStatus.OTHER
            else f"etiqueta de asistencia no validada: {row['attended']!r}",
        )]
