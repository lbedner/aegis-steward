"""Jinja2 filters: the only place templates format money, dates, percents.

Registered on the environment by ``rendering.py``. Amounts arrive from the
finance service as integer minor units with a currency code.
"""

from collections.abc import Callable, Mapping
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


def money(cents: int | None, currency: str = "USD", whole: bool = False) -> str:
    """Minor units -> ``-$1,234.56`` (the Flet register's ``_usd`` rule,
    widened to honour the currency code).

    ``whole`` rounds the cents away for somewhere they are noise rather
    than precision - a month chip reading ``Nov $4,208`` where the
    figure is a projection, not a statement. It is the only reason to
    format money any other way, which is why it lives here instead of in
    the f-string that wanted it.
    """
    code = (currency or "USD").upper()
    if code in _ZERO_DECIMAL_CURRENCIES:
        value, number = cents or 0, f"{abs(cents or 0):,}"
    elif whole:
        value = (cents or 0) / 100
        number = f"{abs(round(value)):,}"
    else:
        value = (cents or 0) / 100
        number = f"{abs(value):,.2f}"
    sign = "-" if value < 0 else ""
    symbol = _CURRENCY_SYMBOLS.get(code)
    return f"{sign}{symbol}{number}" if symbol else f"{sign}{code} {number}"


def dollars(cents: int | None) -> float:
    """Minor units as dollars, for a chart's SCALE.

    Charts are drawn in dollars. Cents on the axis drew a $711,200 house
    at seventy million, and the mistake is invisible until a chart has a
    figure somebody knows by heart - so the conversion has one home
    rather than a ``/ 100`` in every series that gets written.
    """
    return (cents or 0) / 100


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


def as_options(pairs: Any) -> list[dict[str, Any]]:
    """``(key, label)`` pairs, or bare strings, as ``select`` options.

    The macro takes ``{id, name}`` and the constants are pairs, so every
    form that needed one had been hand-rolling its own ``<select>``.
    One shaping, one macro.

    A Mapping is taken as key -> label: the vocabularies that describe a
    choice (``PREPAYMENT``, ``EXTRA_PAYMENT``) are dicts, and iterating
    one yields its keys, which would have put "Unknown" on screen where
    the value says "not confirmed with the lender".
    """
    if isinstance(pairs, Mapping):
        pairs = tuple(pairs.items())
    shaped: list[dict[str, Any]] = []
    for pair in pairs or []:
        if isinstance(pair, str):
            shaped.append({"id": pair, "name": pair.replace("_", " ").capitalize()})
        else:
            key, label = pair
            shaped.append({"id": key, "name": label})
    return shaped


def pct(ratio: float | None, digits: int = 0) -> str:
    """A 0-1 ratio -> ``15%`` (``digits`` decimals). Blank stays blank."""
    if ratio is None:
        return ""
    return f"{ratio * 100:.{digits}f}%"


def cents_to_input(cents: int | None) -> str:
    """The inverse of ``money_to_cents`` for form values: ``350,000.00``."""
    return "" if cents is None else f"{cents / 100:,.2f}"


def arrived(row: Any, batches: Mapping[int, datetime] | None = None) -> Any:
    """When a ledger row came in.

    The run's finishing time when the row came in on one, else the row's
    own ``created_at``. The batch time is the better answer: a 40,000-row
    import writes its rows over several seconds and they all arrived
    together, so ordering by ``created_at`` would scatter one delivery
    across a minute and mark half of it as newer than the other half.

    ``batches`` maps batch id to when it finished - passed in, because a
    page draws fifty rows and must not ask the database fifty times.
    """
    batch_id = getattr(row, "import_batch_id", None)
    if batch_id is None and isinstance(row, Mapping):
        batch_id = row.get("import_batch_id")
    if batch_id is not None and batches:
        finished = batches.get(batch_id)
        if finished is not None:
            return finished
    created = getattr(row, "created_at", None)
    if created is None and isinstance(row, Mapping):
        created = row.get("created_at")
    return created


async def arrivals_by_account(
    db: Any, since: datetime | None, account_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Per account, the latest row that arrived after ``since`` - marked.

    The portfolio's answer to "did the import land": the register marks
    the rows, and this marks the accounts they landed in, by the same
    rule and the same watermark. One query, bounded by the watermark
    rather than the ledger - only rows newer than the last look can be
    new, so those are the only ones fetched.
    """
    if since is None or not account_ids:
        return {}
    from sqlmodel import col, select

    from app.services.finance.models import FinanceTransaction

    floor = since.replace(tzinfo=None) if since.tzinfo else since
    rows = (
        await db.exec(
            select(
                FinanceTransaction.account_id,
                FinanceTransaction.created_at,
                FinanceTransaction.import_batch_id,
            )
            .where(col(FinanceTransaction.account_id).in_(account_ids))
            .where(col(FinanceTransaction.deleted_at).is_(None))
            .where(FinanceTransaction.created_at > floor)
            .order_by(col(FinanceTransaction.created_at).desc())
        )
    ).all()
    latest: dict[int, dict[str, Any]] = {}
    for account_id, created_at, batch_id in rows:
        latest.setdefault(
            account_id, {"created_at": created_at, "import_batch_id": batch_id}
        )
    marked = await mark_new(db, list(latest.values()), since)
    return {
        account_id: row
        for account_id, row in zip(latest, marked, strict=True)
        if row.get("is_new")
    }


async def mark_new(
    db: Any, rows: list[dict[str, Any]], since: datetime | None
) -> list[dict[str, Any]]:
    """Flag the rows that arrived after ``since``.

    One pass, one query: the batches these rows came in on are fetched
    together, because a page of fifty rows must not ask fifty times when
    its delivery finished.

    No watermark means no marks. A ledger that lights up entirely on a
    first visit has told the reader nothing.
    """
    if since is None or not rows:
        return rows
    ids = {row.get("import_batch_id") for row in rows} - {None}
    finished: dict[int, datetime] = {}
    if ids:
        from sqlmodel import select

        from app.services.finance.models.imports import FinanceImportBatch

        found = (
            await db.exec(
                select(FinanceImportBatch).where(FinanceImportBatch.id.in_(ids))
            )
        ).all()
        finished = {b.id: b.finished_at or b.started_at for b in found if b.id}
    for row in rows:
        when = arrived(row, finished)
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        row["is_new"] = bool(when and when > since)
    return rows


# How old a figure may get before it is worth saying so, keyed on how
# often the thing is EXPECTED to change. Not on how much we care: a
# threshold set by taste drifts every time somebody's taste does.
#
#   amber - older than one cycle. Something may have been missed.
#   red   - older than several. Something is wrong, or nobody is looking.
#
# Anything fed by a daily sync or a nightly import shares one answer.
# Three names for the same two numbers is three places to edit when the
# answer changes, and two of them will be missed.
DAILY = (7, 30)
# A house is valued when somebody asks, which is a few times a year.
# Amber at four months and red past a year, because a valuation from
# June is a normal thing to be holding in September - and a colour that
# cries stale at every ordinary age teaches the reader to ignore it.
QUARTERLY = (120, 400)

STALE_AFTER: dict[str, tuple[int, int]] = {
    "sync": DAILY,
    "holdings": DAILY,
    "valuation": QUARTERLY,
    "default": DAILY,
}


def freshness(
    when: date | datetime | str | None,
    kind: str = "default",
    today: date | None = None,
    after: int | None = None,
) -> dict[str, str]:
    """How current a figure is: the words, and a tone for how it reads.

    ``{"label": "2 days ago", "tone": "ok"}``. One helper because the
    connection card, the account header and a holdings table all answer
    "is this still true?" and must not disagree about when the answer
    becomes no.

    Nothing at all is not stale - it is unknown, which is a different
    thing and says so.

    ``after`` is a row's own answer, overriding its kind's: a valuation
    carries ``stale_after_days`` because a quarterly appraisal and a
    daily quote are both valuations. It is the ONE staleness rule either
    way - the column used to imply a second mechanism, and a stored
    ``is_stale`` boolean is wrong the day after it is written.
    """
    if not when:
        return {"label": "never", "tone": "muted"}
    if isinstance(when, str):
        try:
            when = datetime.fromisoformat(when)
        except ValueError:
            return {"label": str(when), "tone": "muted"}
    seen = when.date() if isinstance(when, datetime) else when
    days = ((today or _utc_today()) - seen).days
    warn, bad = STALE_AFTER.get(kind, STALE_AFTER["default"])
    if after is not None:
        warn, bad = after, after
    tone = "ok" if days <= warn else ("warn" if days <= bad else "error")
    if days <= 0:
        label = "today"
    elif days == 1:
        label = "yesterday"
    elif days < 30:
        label = f"{days} days ago"
    elif days < 365:
        label = f"{days // 30} month{'s' if days // 30 != 1 else ''} ago"
    else:
        label = f"{days // 365} year{'s' if days // 365 != 1 else ''} ago"
    return {"label": label, "tone": tone}


def positions_from_text(raw: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Pasted positions -> rows, and a complaint per line that is not one.

    ``VOO 14.2 512.30`` a line: ticker, shares, unit price. Commas,
    dollar signs, tabs and multiple spaces are all normal in a paste off
    a statement or a brokerage screen, so they are taken out rather than
    rejected. The price is optional - a position with no price still
    tells you what is held.

    Bad lines are REPORTED, not skipped: a paste of twenty rows where two
    are malformed must not silently file eighteen.
    """
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line in (raw or "").splitlines():
        cleaned = line.replace("$", "").replace(",", "").strip()
        if not cleaned:
            continue
        parts = cleaned.split()
        if len(parts) < 2:
            errors.append(f"{line.strip()} - needs a ticker and a quantity")
            continue
        ticker, quantity, *rest = parts
        try:
            shares = float(quantity)
        except ValueError:
            errors.append(f"{line.strip()} - {quantity!r} is not a quantity")
            continue
        price: int | None = None
        if rest:
            price = money_to_cents(rest[0])
            if price is None:
                errors.append(f"{line.strip()} - {rest[0]!r} is not a price")
                continue
        rows.append({"ticker": ticker.upper(), "quantity": shares, "price": price})
    return rows, errors


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
    "as_options": as_options,
    "money": money,
    "cents_to_input": cents_to_input,
    "short_date": short_date,
    "date_range": date_range,
    "pct": pct,
    "markdown": markdown,
    "code": code,
    "assistant": assistant,
}
