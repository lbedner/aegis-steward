"""What a property is worth: the value history, its chart, and where the
purchase sits on the line.

Split out of ``accounts.py`` at the 500-line budget. A value history is
the one ledger read in decades rather than months, and the rules that
follow from that - a window relative to the purchase, a mark found by
PRICE because the recorded purchase date can be wrong - are their own
subject rather than the account page's.
"""

from __future__ import annotations

from typing import Any

from app.components.web_frontend import ranges
from app.components.web_frontend.filters import dollars, money
from app.services.finance.schemas import AccountResponse
from app.services.finance.service import FinanceService

VALUATION_COLUMNS = (
    {"key": "as_of_date", "label": "Date", "kind": "date"},
    {"key": "note", "label": "Event"},
    {"key": "source", "label": "Source"},
    {"key": "value", "label": "Value", "kind": "money", "align": "right"},
)


async def valuation_history(
    service: FinanceService, account: AccountResponse, owner_user_id: int | None
) -> list[dict[str, Any]]:
    """An asset's value over time, newest first.

    On the PAGE rather than only behind a Manage dialog: what a house
    fell to and what it recovered to is the reason for keeping the
    history, and a history nobody passes is a history nobody reads.
    """
    from app.services.finance.domains.ledger.valuations import list_valuations

    if account.classification != "asset":
        return []
    found = await list_valuations(
        service.db, account.id, owner_user_id=owner_user_id
    )
    return [
        valuation_row(row)
        for row in sorted(found, key=lambda v: v.as_of_date, reverse=True)
    ]





def valuation_chart(
    history: list[dict[str, Any]], account: AccountResponse | None = None
) -> dict[str, Any] | None:
    """An asset's value over time, with the day the OWNER bought it
    marked.

    Oldest first, because a line reads forwards. One mark, because every
    other number on the line is only interesting relative to what was
    actually paid - and a mark per event turns a few hundred monthly
    estimates into a picket fence.

    Which point is the purchase comes from the PRICE the owner recorded,
    not from a note. A price history describes the property and says
    "Sold" about every owner it ever had; the first one of those was a
    stranger's purchase in 2007, and that is where the dot landed.
    """
    if len(history) < 2:
        return None
    series = sorted(history, key=lambda row: row["as_of_date"])
    return {
        "labels": [f"{row['as_of_date']:%b %Y}" for row in series],
        # Dollars, like every other chart in the app. Cents here drew a
        # $711,200 house at seventy million.
        "series": [
            {"label": "Value", "values": [dollars(row["value"]) for row in series]}
        ],
        "events": purchase_mark(series, account),
    }


def purchase_index(
    series: list[dict[str, Any]], account: AccountResponse | None
) -> int | None:
    """Where in this series the owner bought it, by the price they
    recorded. See ``purchase_mark`` for why the price and not the date."""
    paid = getattr(getattr(account, "property", None), "purchase_price", None)
    if not paid:
        return None
    return next(
        (i for i, row in enumerate(series) if row["value"] == paid), None
    )


def in_window(
    history: list[dict[str, Any]], days: int, account: AccountResponse | None
) -> list[dict[str, Any]]:
    """The history a window asks for.

    Most windows are a number of days back from today. Two are not: "the
    run-up to buying it" and "everything since" are positions in this
    asset's own story, and a house is the one thing people ask about
    that way - what it had been doing before they walked in.
    """
    if ranges.relative_to_purchase(days):
        ordered = sorted(history, key=lambda row: row["as_of_date"])
        at = purchase_index(ordered, account)
        if at is None:
            return history
        # The purchase itself belongs to BOTH halves: it is the end of
        # the run-up and the start of what you have done with it.
        window = ordered[: at + 1] if days == ranges.BEFORE_PURCHASE else ordered[at:]
        return sorted(window, key=lambda row: row["as_of_date"], reverse=True)
    start = ranges.since(days)
    if start is None:
        return history
    return [row for row in history if row["as_of_date"] >= start]


def purchase_mark(
    series: list[dict[str, Any]], account: AccountResponse | None
) -> list[dict[str, Any]]:
    """The chart's one annotation: where the owner bought it.

    Matched on the recorded purchase PRICE rather than the recorded
    DATE, because the price is the figure people keep accurately and the
    date is the one that drifts - on the ledger this was written for,
    the price was exactly right and the date was nine months out. Where
    several points share the price, the earliest is the purchase and the
    rest are a valuation that happens to agree with it.
    """
    paid = getattr(getattr(account, "property", None), "purchase_price", None)
    if not paid:
        return []
    for index, row in enumerate(series):
        if row["value"] == paid:
            return [{"at": index, "label": f"Bought · {money(paid)}"}]
    return []


def valuation_row(row: Any) -> dict[str, Any]:
    """One valuation, as the history table draws it.

    Takes the model row or the response shape: the page reads one and
    the Manage dialog the other, and a table drawn two ways is a table
    that will disagree with itself. ``is_estimate`` is only on the
    model, so it is asked for rather than assumed.
    """
    estimate = getattr(row, "is_estimate", False)
    return {
        "as_of_date": row.as_of_date,
        "note": row.note or ("Estimate" if estimate else "-"),
        "source": row.source,
        "value": row.value,
        # What the mark reads. The Manage dialog passes the response
        # shape, which carries neither, so both are asked for rather
        # than assumed - the same reason ``is_estimate`` is.
        "import_batch_id": getattr(row, "import_batch_id", None),
        "created_at": getattr(row, "created_at", None),
    }
