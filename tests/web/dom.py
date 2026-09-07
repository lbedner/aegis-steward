"""DOM assertions for rendered HTML.

Page tests select elements and read their text instead of grepping the raw
response for substrings. A substring check passes when the words appear in a
comment or the wrong element, and fails on a harmless attribute reorder;
selectors do neither.

    from tests.web.dom import one, select, text

    row = one(resp.text, "#txn-42")
    assert text(one(row, "td.amount")) == "$12.00"
"""

from __future__ import annotations

from lxml import html
from lxml.html import HtmlElement

Markup = str | HtmlElement


def _root(markup: Markup) -> HtmlElement:
    if isinstance(markup, HtmlElement):
        return markup
    # ``fragment_fromstring`` would reject multi-root fragments; wrapping
    # gives one root for full pages and fragments alike.
    return html.fromstring(f"<div>{markup}</div>")


def select(markup: Markup, css: str) -> list[HtmlElement]:
    """Every element matching ``css``. Empty list on no match."""
    return _root(markup).cssselect(css)


def one(markup: Markup, css: str) -> HtmlElement:
    """The single element matching ``css``; fails on zero or many."""
    found = select(markup, css)
    assert len(found) == 1, f"expected 1 element for {css!r}, got {len(found)}"
    return found[0]


def text(element: HtmlElement) -> str:
    """Visible text of ``element`` and its descendants, whitespace collapsed."""
    return " ".join(element.text_content().split())


def is_fragment(markup: str) -> bool:
    """True when ``markup`` is a bare fragment rather than a full document."""
    lowered = markup.lstrip().lower()
    return not (lowered.startswith("<!doctype") or lowered.startswith("<html"))
