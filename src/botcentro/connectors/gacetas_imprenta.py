"""SRC-03: Gacetas del Congreso en la Imprenta Nacional (docs/fuentes/SRC-03.md).

Fuente: https://svrpubindc.imprenta.gov.co/senado/ (JSF/PrimeFaces, sin robots.txt ni términos).

* Listado: tabla paginada por AJAX de PrimeFaces sobre el formulario `formResumen`; cada fila trae
  número, entidad («Senado de la República» o «Cámara de Representantes») y fecha. La respuesta
  parcial llega en ISO-8859-1.
* Descarga: enlace permanente `index2.xhtml?ent=…&fec=d-m-aaaa&num=…` cuyo formulario `dldFile`
  entrega el PDF. `ent` va en ISO-8859-1 («C%E1mara»); en UTF-8 la fuente no encuentra la gaceta.
* Los PDF no se conservan (DEC-11): se guardan hash, texto derivado y el enlace oficial.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from botcentro.errors import FailureKind, FetchError
from botcentro.http.fetcher import Fetched, NotModified, SafeFetcher

BASE = "https://svrpubindc.imprenta.gov.co/senado/"
DOMAINS = ("svrpubindc.imprenta.gov.co",)
TABLE = "formResumen:dataTableResumen"
_FORM_RE = re.compile(r'<form id="(?P<form>formResumen|dldFile)"[^>]*action="(?P<action>[^"]+)"')
_STATE_RE = re.compile(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"')
_PARTIAL_STATE_RE = re.compile(r'<update id="[^"]*javax\.faces\.ViewState[^"]*"><!\[CDATA\[([^\]]+)\]\]>')
_ROW_RE = re.compile(
    r'<tr data-ri="(?P<ri>\d+)"[^>]*>.*?j_idt13"[^>]*>(?P<num>[^<]*)<.*?j_idt18"[^>]*>(?P<ent>[^<]*)<'
    r'.*?j_idt20"[^>]*>(?P<fec>[^<]*)<', re.S)
_TOTAL_RE = re.compile(r"Registro \d+ a \d+ de (\d+)")
_ENTITIES = {"Senado de la República": "senado", "Cámara de Representantes": "camara"}
_PARAM = {"senado": "Senado", "camara": "Cámara"}


@dataclass(frozen=True)
class GacetaRef:
    corporation: str  # 'senado' | 'camara'
    published_on: date
    number: str

    @property
    def document_key(self) -> str:
        return f"gaceta:{self.corporation}:{self.published_on.year}:{self.number}"

    @property
    def permalink(self) -> str:
        d = self.published_on
        ent = quote(_PARAM[self.corporation], encoding="latin-1")
        return f"{BASE}index2.xhtml?ent={ent}&fec={d.day}-{d.month}-{d.year}&num={self.number}"


def _html(fetched: Fetched | NotModified, encoding: str = "utf-8") -> str:
    if not isinstance(fetched, Fetched):
        raise FetchError("UNEXPECTED_304", "la fuente respondió 304 sin validadores", kind=FailureKind.SCHEMA_CHANGED)
    return fetched.content.decode(encoding, errors="replace")


def parse_rows(markup: str) -> list[GacetaRef]:
    refs = []
    for m in _ROW_RE.finditer(markup):
        corporation = _ENTITIES.get(m["ent"].strip())
        day, month, year = (int(x) for x in m["fec"].strip().split("/"))
        if corporation and m["num"].strip():
            refs.append(GacetaRef(corporation, date(year, month, day), m["num"].strip()))
    return refs


class GacetaListing:
    """Recorre el listado (más reciente primero) conservando la sesión JSF."""

    def __init__(self, fetcher: SafeFetcher) -> None:
        self.fetcher = fetcher
        page = _html(fetcher.fetch(BASE))
        form = _FORM_RE.search(page)
        state = _STATE_RE.search(page)
        total = _TOTAL_RE.search(page)
        if not (form and state and total):
            raise FetchError("SCHEMA_CHANGED", "el listado de gacetas cambió de estructura", kind=FailureKind.SCHEMA_CHANGED)
        self.action = "https://svrpubindc.imprenta.gov.co" + form["action"].replace("&amp;", "&")
        self.state = state.group(1)
        self.total = int(total.group(1))

    def page(self, first: int, rows: int = 50) -> list[GacetaRef]:
        response = _html(self.fetcher.fetch(self.action, method="POST", data={
            "javax.faces.partial.ajax": "true", "javax.faces.source": TABLE,
            "javax.faces.partial.execute": TABLE, "javax.faces.partial.render": TABLE,
            f"{TABLE}_pagination": "true", f"{TABLE}_first": str(first), f"{TABLE}_rows": str(rows),
            f"{TABLE}_encodeFeature": "true", "formResumen": "formResumen", "javax.faces.ViewState": self.state,
        }), "latin-1")
        if new_state := _PARTIAL_STATE_RE.search(response):
            self.state = new_state.group(1)
        refs = parse_rows(response)
        if not refs and first < self.total:
            raise FetchError("SCHEMA_CHANGED", "página del listado sin filas reconocibles", kind=FailureKind.SCHEMA_CHANGED)
        return refs


def download(fetcher: SafeFetcher, ref: GacetaRef) -> Fetched:
    page = _html(fetcher.fetch(ref.permalink))
    form, state = _FORM_RE.search(page), _STATE_RE.search(page)
    if not (form and form["form"] == "dldFile" and state):
        raise FetchError("SCHEMA_CHANGED", "el enlace permanente de la gaceta cambió", kind=FailureKind.SCHEMA_CHANGED)
    fetched = fetcher.fetch("https://svrpubindc.imprenta.gov.co" + form["action"], method="POST",
                            data={"dldFile": "dldFile", "pdfIr": "pdfIr", "javax.faces.ViewState": state.group(1)})
    if not isinstance(fetched, Fetched) or not fetched.content.startswith(b"%PDF"):
        raise FetchError("NOT_FOUND", f"la fuente no entregó el PDF de {ref.document_key}", kind=FailureKind.NOT_FOUND)
    return fetched
