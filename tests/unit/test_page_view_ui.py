"""«📄 Ver página»: vista de foto, navegación ◀ ▶ sin alargar el historial y fallos explícitos."""

from pathlib import Path

from botcentro.documents.page_render import PageUnavailable, RenderedPage
from botcentro.telegram_ui.app import UiApplication
from botcentro.telegram_ui.contracts import Frame, Intent, SessionContext, UiAction

KEY = "gaceta:senado:2026:1151"
URL = "https://svrpubindc.imprenta.gov.co/senado/index2.xhtml?ent=Senado&fec=27-8-2026&num=1151"


class FakeEngine:
    rpc = None


class FakePages:
    def __init__(self, pages: int = 40, fail: str | None = None) -> None:
        self.pages, self.fail, self.calls = pages, fail, []

    def render(self, document_key, url, page):
        self.calls.append((document_key, url, page))
        if self.fail:
            raise PageUnavailable(self.fail)
        return RenderedPage(Path(f"/cache/p{page}.png"), page, self.pages, "sha", False)


def _click(app, ctx, page):
    return app.handle(UiAction(intent=Intent.PAGE_VIEW, entry_point="button",
                               parameters={"document_key": KEY, "url": URL, "page": page, "label": "acta 5/2026"}), ctx)


def _labels(view):
    return [b.label for row in view.rows for b in row]


def test_page_is_a_photo_with_caption_navigation_and_official_link() -> None:
    app = UiApplication(FakeEngine(), pages=FakePages())  # type: ignore[arg-type]
    view = _click(app, SessionContext(), 12)
    assert view.photo == {"path": "/cache/p12.png"}
    assert "Gaceta 1151/2026 (Senado)" in view.blocks[0] and "página 12 de 40" in view.blocks[0]
    assert "acta 5/2026" in view.blocks[0]
    assert {"◀ p. 11", "p. 13 ▶", "🔗 Gaceta oficial"} <= set(_labels(view))
    official = next(b for row in view.rows for b in row if b.label == "🔗 Gaceta oficial")
    assert official.url == URL


def test_first_and_last_page_hide_the_impossible_arrow() -> None:
    app = UiApplication(FakeEngine(), pages=FakePages(pages=2))  # type: ignore[arg-type]
    assert not any(l.startswith("◀") for l in _labels(_click(app, SessionContext(), 1)))
    assert not any(l.endswith("▶") for l in _labels(_click(app, SessionContext(), 2)))


def test_paging_does_not_grow_history_so_back_returns_to_results() -> None:
    app = UiApplication(FakeEngine(), pages=FakePages())  # type: ignore[arg-type]
    results = Frame(intent=Intent.ACTA_SEARCH, params={"query": "salud"}, title="Actas")
    ctx = SessionContext(current=results)
    _click(app, ctx, 12)
    _click(app, ctx, 13)
    _click(app, ctx, 14)
    assert ctx.current.intent is Intent.PAGE_VIEW and ctx.current.params["page"] == 14
    assert ctx.navigation_stack == [results]


def test_unavailable_page_says_why_and_offers_the_source() -> None:
    app = UiApplication(FakeEngine(), pages=FakePages(fail="la gaceta tiene 40 páginas"))  # type: ignore[arg-type]
    view = _click(app, SessionContext(), 99)
    assert view.photo is None and view.status == "unavailable"
    assert "la gaceta tiene 40 páginas" in view.blocks[0] and "🔗 Gaceta oficial" in _labels(view)
