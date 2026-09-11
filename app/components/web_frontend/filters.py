"""Jinja2 filters: the only place templates format money, dates, percents.

Registered on the environment by ``rendering.py``. Amounts arrive from the
finance service as integer minor units with a currency code.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
import html
from typing import Any

from markupsafe import Markup

from app.core.formatting import format_date_range

# Symbols for the codes a household ledger actually sees; anything else
# shows its code.
_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
_ZERO_DECIMAL_CURRENCIES = {"JPY", "KRW"}


def _utc_today() -> date:
    """Today in UTC, the clock the rest of the app stamps rows with.

    Spelled out rather than imported from a service: the web frontend
    ships in projects that have none of them.
    """
    return datetime.now(UTC).date()


def money(cents: int | None, currency: str = "USD") -> str:
    """Minor units -> ``-$1,234.56`` (the Flet register's ``_usd`` rule,
    widened to honour the currency code)."""
    code = (currency or "USD").upper()
    if code in _ZERO_DECIMAL_CURRENCIES:
        value, number = cents or 0, f"{abs(cents or 0):,}"
    else:
        value = (cents or 0) / 100
        number = f"{abs(value):,.2f}"
    sign = "-" if value < 0 else ""
    symbol = _CURRENCY_SYMBOLS.get(code)
    return f"{sign}{symbol}{number}" if symbol else f"{sign}{code} {number}"


def short_date(value: date | datetime | str | None, today: date | None = None) -> str:
    """``Jul 15`` this year, ``Jul 15, 2025`` otherwise. Blank stays blank."""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    label = f"{value:%b} {value.day}"
    if value.year == (today or _utc_today()).year:
        return label
    return f"{label}, {value.year}"


def date_range(start: date | str | None, end: date | str | None) -> str:
    """The span two dates cover, on one line (``format_date_range``)."""
    return format_date_range(start, end)


def pct(ratio: float | None, digits: int = 0) -> str:
    """A 0-1 ratio -> ``15%`` (``digits`` decimals). Blank stays blank."""
    if ratio is None:
        return ""
    return f"{ratio * 100:.{digits}f}%"


def cents_to_input(cents: int | None) -> str:
    """The inverse of ``money_to_cents`` for form values: ``350,000.00``."""
    return "" if cents is None else f"{cents / 100:,.2f}"


def money_to_cents(raw: str | None) -> int | None:
    """``"$1,200.50"`` / ``"3,000"`` / ``" 12 "`` -> cents; blank -> 0;
    anything else -> ``None`` (the caller decides that is a 422)."""
    cleaned = (raw or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        return 0
    try:
        return round(float(cleaned) * 100)
    except ValueError:
        return None


# A settled assistant message is markdown from a model. marko renders it
# (GFM: tables, strikethrough, autolinks); raw HTML in the source is
# escaped rather than passed through, so the model can format but never
# inject markup. The mixin is registered LAST so it sits first in the
# renderer's MRO, ahead of GFM's own tag filter.
def _safe_markdown() -> Any:
    from marko import Markdown
    from marko.ext.gfm import GFM
    from marko.helpers import MarkoExtension

    class EscapeHTML:
        def render_html_block(self, element: Any) -> str:
            return html.escape(element.body)

        def render_inline_html(self, element: Any) -> str:
            return html.escape(element.children)

    return Markdown(extensions=[GFM, MarkoExtension(renderer_mixins=[EscapeHTML])])


_MARKDOWN = _safe_markdown()


def markdown(text: str | None) -> Markup:
    """Model markdown as HTML, with any raw HTML in it escaped."""
    return Markup(_MARKDOWN.convert(text or ""))


# A run's script, highlighted with the classes ``input.css`` colours from
# the theme tokens (``.hl .k`` and friends); pygments ships with the CLI's
# renderer already.
def _highlighter() -> Any:
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import JsonLexer, PythonLexer

    formatter = HtmlFormatter(nowrap=True)
    lexers = {"python": PythonLexer(), "json": JsonLexer()}
    return lambda code, lang: highlight(code, lexers[lang], formatter)


_HIGHLIGHT = _highlighter()


def code(source: str | None, lang: str = "python") -> Markup:
    """Source as highlighted HTML spans (no wrapper, no styles); ``lang``
    is python or json."""
    return Markup(_HIGHLIGHT(source or "", lang))


# The assistant's name, by agent slug. The queue stamps a proposal with
# the slug of the agent that made it; a person reads the name. One map,
# shared by the chat page (which names its assistant) and every badge.
ASSISTANTS = {"finance-assistant": "Illiana"}


def assistant(slug: str | None) -> str:
    """The assistant's display name for an agent slug, else the slug."""
    return ASSISTANTS.get(slug or "", slug or "")


FILTERS: dict[str, Callable[..., str]] = {
    "money": money,
    "cents_to_input": cents_to_input,
    "short_date": short_date,
    "date_range": date_range,
    "pct": pct,
    "markdown": markdown,
    "code": code,
    "assistant": assistant,
}
