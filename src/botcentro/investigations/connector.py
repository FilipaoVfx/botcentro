"""Conector Socrata para el `IngestionRunner` existente (ING-01..04, SRC-04; reutiliza §5 del SRS).

* Corte congelado: la primera página fija `cutoff` (`:updated_at <= cutoff`); lo que cambie durante
  la ejecución no mueve el límite y entra en la siguiente, con el solapamiento incremental (T-11).
* Paginación por clave (`:id > after`) y orden `:id`: filas actualizadas o borradas a mitad de la
  ejecución no desplazan páginas (T-12). `:id` solo pagina; nunca es identidad (ING-09).
* Límites de filas, solicitudes o minutos: la página marca `limit_reached` y el runner termina en
  `partial` con el cursor listo para continuar (T-13). Nunca éxito completo.
* El esquema se valida antes de la primera página: un campo crítico ausente detiene (T-06).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from botcentro.connectors.base import (Cursor, DiscoveredItem, DiscoverPage, Issue, IssueSeverity, ParseResult,
                                       ValidationReport)
from botcentro.http.fetcher import Fetched, NotModified, SafeFetcher
from botcentro.investigations.datasets import MAPPING_VERSION, DatasetSpec
from botcentro.investigations.socrata import BASE, PAGE_SIZE, Condition, Query, SchemaIncompatible, check_schema

JSON_ONLY = frozenset({"application/json", "text/plain"})


class SocrataConnector:
    version = "0.1.0"

    def __init__(self, fetcher: SafeFetcher, spec: DatasetSpec, *, identity_key: bytes | None = None,
                 max_rows: int | None = None, max_requests: int | None = None, max_minutes: float | None = None,
                 page_size: int = PAGE_SIZE, clock=time.monotonic, sleep=time.sleep, pause_seconds: float = 1.0,
                 max_attempts: int = 5) -> None:  # noqa: ANN001
        self.fetcher = fetcher
        self.spec = spec
        self.adapter = spec.adapter
        self.parser_version = f"{spec.adapter}-parser-1+{MAPPING_VERSION}"
        key = identity_key or os.environ.get("BOTCENTRO_IDENTITY_KEY", "").encode()
        if len(key) < 32:
            raise ValueError("falta BOTCENTRO_IDENTITY_KEY (≥ 32 bytes) para proteger documentos de personas")
        self.key = key
        self.max_rows = max_rows or int(os.environ.get("PILOT_MAX_RUN_ROWS", "10000"))
        self.max_requests = max_requests or int(os.environ.get("PILOT_MAX_RUN_REQUESTS", "100"))
        self.max_minutes = max_minutes or float(os.environ.get("PILOT_MAX_RUN_MINUTES", "30"))
        self.page_size = page_size
        self.clock = clock
        self.sleep = sleep
        self.pause_seconds = pause_seconds
        self.max_attempts = max_attempts
        self._last_request = 0.0
        self._started = clock()
        self._last_after: str | None = None
        self._last_rows: int | None = None
        self._awaiting_parse = False  # se entregó una página que no llegó a interpretarse (falló la descarga)
        self.schema = None

    # -- esquema -------------------------------------------------------------------------------

    def validate_source(self, config: Mapping[str, Any] | None = None) -> ValidationReport:
        fetched = self.fetcher.fetch(f"{BASE}/api/views/{self.spec.dataset_id}.json", accept_mimes=JSON_ONLY)
        assert isinstance(fetched, Fetched)
        self.schema = check_schema(json.loads(fetched.content), self.spec.critical, self.spec.optional)
        if self.schema.missing_critical:
            raise SchemaIncompatible(self.schema.missing_critical, self.schema.fingerprint)
        diagnostics = [f"campo opcional ausente: {f}" for f in self.schema.missing_optional]
        return ValidationReport(capabilities=[k for k, v in self.spec.capabilities.items() if v not in ("unsupported",)],
                                diagnostics=diagnostics)

    # -- descubrimiento ------------------------------------------------------------------------

    def _query(self, scope: Mapping[str, Any], cutoff: str, after: str | None) -> Query:
        allowed = self.spec.fields - frozenset(self.schema.missing_optional if self.schema else ())
        conditions, groups = self.spec.scope_conditions(scope)
        conditions.append(Condition(":updated_at", "<=", cutoff))
        if after:
            conditions.append(Condition(":id", ">", after))
        return Query(allowed_fields=allowed | {":id", ":updated_at"}, conditions=conditions, any_of=groups,
                     order=[":id"], limit=self.page_size)

    def discover(self, cursor: Cursor) -> DiscoverPage:
        position = dict(cursor.position)
        if self._awaiting_parse:
            # La página anterior falló: no se reintenta en bucle ni se da por vacía. La ejecución
            # termina en `partial` (el runner contó el fallo) y el cursor permite reanudar.
            self._awaiting_parse = False
            return DiscoverPage([], cursor, has_more=False, limit_reached="una página falló; reanudar desde el cursor")
        if "cutoff" not in position:
            position.update(cutoff=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), rows=0, requests=0)
        after = self._last_after if self._last_after is not None else position.get("after")
        rows = int(position.get("rows", 0)) + (self._last_rows or 0)
        requests = int(position.get("requests", 0)) + (1 if self._last_rows is not None else 0)
        finished = self._last_rows is not None and self._last_rows < self.page_size
        position.update(after=after, rows=rows, requests=requests)
        next_cursor = cursor.advance(**position)
        if finished:
            return DiscoverPage([], next_cursor, has_more=False)
        elapsed_min = (self.clock() - self._started) / 60
        if rows >= self.max_rows or requests >= self.max_requests or elapsed_min >= self.max_minutes:
            reason = ("filas" if rows >= self.max_rows else "solicitudes" if requests >= self.max_requests else "minutos")
            return DiscoverPage([], next_cursor, has_more=False,
                                limit_reached=f"límite de {reason} por ejecución; continúa desde el cursor")
        query = self._query(cursor.scope, position["cutoff"], after)
        params = {**query.params(), "$select": "*, :id"}  # Socrata exige la selección «*» al inicio
        url = f"{BASE}/resource/{self.spec.dataset_id}.json?{urlencode(params)}"
        item = DiscoveredItem(record_type=f"{self.spec.adapter}_page", logical_key=f"{self.spec.adapter}:{after or 'inicio'}",
                              url=url, accept_mimes=JSON_ONLY, hints={"after": after})
        self._last_rows = None
        self._awaiting_parse = True
        return DiscoverPage([item], next_cursor, has_more=True)

    def fetch(self, item: DiscoveredItem, *, etag: str | None = None, last_modified: str | None = None) -> Fetched | NotModified:
        """Una solicitud a la vez por fuente, con pausa entre páginas (ING-06) y reintentos acotados
        (ING-05): Retry-After, backoff exponencial con variación y máximo cinco intentos."""
        import random

        from botcentro.errors import FetchError
        from botcentro.investigations.results import ErrorCode
        from botcentro.investigations.socrata import classify

        elapsed = time.monotonic() - self._last_request
        if elapsed < self.pause_seconds:
            self.sleep(self.pause_seconds - elapsed)
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = self.fetcher.fetch(item.url, accept_mimes=JSON_ONLY)
                self._last_request = time.monotonic()
                self._track_page(result)
                return result
            except FetchError as exc:
                self._last_request = time.monotonic()
                retryable = classify(exc) in (ErrorCode.TIMEOUT, ErrorCode.RATE_LIMITED, ErrorCode.UNAVAILABLE)
                if not retryable or attempt == self.max_attempts:
                    raise
                self.sleep(min(exc.retry_after or (2 ** attempt) * (0.5 + random.random() / 2), 60))
        raise AssertionError("inalcanzable")

    def _track_page(self, result: Fetched | NotModified) -> None:
        """La paginación avanza con lo descargado, no con lo interpretado: una página idéntica a la de
        una ejecución anterior («sin cambios») no se reinterpreta, pero sí mueve el cursor."""
        if not isinstance(result, Fetched):
            return
        try:
            rows = json.loads(result.content)
        except ValueError:
            return
        if isinstance(rows, list):
            self._awaiting_parse = False
            self._last_rows = len(rows)
            if rows:
                self._last_after = str(rows[-1].get(":id") or "")

    # -- parseo --------------------------------------------------------------------------------

    def parse(self, item: DiscoveredItem, snapshot: Fetched) -> ParseResult:
        self._awaiting_parse = False
        try:
            rows = json.loads(snapshot.content)
        except ValueError:
            self._last_rows = 0
            return ParseResult(issues=[Issue("PARSE_ERROR", "la página no es JSON", IssueSeverity.SCHEMA_CHANGE)])
        if not isinstance(rows, list):
            self._last_rows = 0
            return ParseResult(issues=[Issue("INVALID_CONTENT", "la página no es una lista", IssueSeverity.SCHEMA_CHANGE)])
        candidates, issues = [], []
        for row in rows:
            try:
                candidates.append(self.spec.parser(row, self.key))
            except (ValueError, TypeError, KeyError) as exc:
                issues.append(Issue("ROW_REJECTED", str(exc)[:200], IssueSeverity.WARNING, pointer=str(row.get(":id"))))
        return ParseResult(candidates=candidates, issues=issues, empty_source=not rows)
