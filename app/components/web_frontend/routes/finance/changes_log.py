"""Every edit, in words, and the last one undone (#325).

Settings > Changes lists what was done, newest first, one action to a
line - who, when, on what - with Undo on the newest that can be taken
back. A register row's History is the same log for one transaction.
Values read as the page shows them: a payee by its name, money as money.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.filters import money, short_date
from app.components.web_frontend.nav import settings_nav as nav_context
from app.components.web_frontend.rendering import dialog, render, with_toast
from app.components.web_frontend.routes.finance.settings import SECTION
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger import changelog
from app.services.finance.domains.ledger.queries.accounts import account_names
from app.services.finance.domains.ledger.queries.categories import (
    category_names_by_id,
)
from app.services.finance.domains.ledger.queries.merchants import merchants_by_ids
from app.services.finance.domains.ledger.queries.transactions import transactions_by_ids
from app.services.finance.models import (
    FinanceTag,
    FinanceTransactionChangelog,
)
from app.services.finance.service import FinanceService

router = APIRouter()
CHANGES = f"{SECTION.path}/changes"

_WHO = {"you": "You", changelog.IMPORT: "An import", changelog.SYNC: "A sync"}
_WHO[changelog.UNDO] = "Undo"
# The fields whose values are ids, read as the names they point at.
_NAMED = {"merchant_id", "category_id", "account_id", "tag"}


async def _names(
    db: AsyncSession, rows: Iterable[FinanceTransactionChangelog]
) -> dict[str, dict[int, str]]:
    """The names the ids in ``rows`` read as, one read per kind."""
    wanted: dict[str, set[int]] = {}
    for row in rows:
        if row.field in _NAMED:
            for value in (row.old_value, row.new_value):
                if value is not None:
                    wanted.setdefault(row.field, set()).add(int(value))
    tags = wanted.get("tag", set())
    return {
        "merchant_id": {
            i: m.name
            for i, m in (
                await merchants_by_ids(db, wanted.get("merchant_id", set()))
            ).items()
        },
        "category_id": await category_names_by_id(db, wanted.get("category_id", set())),
        "account_id": await account_names(db, list(wanted.get("account_id", set()))),
        "tag": {
            t.id: t.name
            for t in (
                await db.exec(select(FinanceTag).where(FinanceTag.id.in_(tags)))
            ).all()
        }
        if tags
        else {},
    }


def _said(field: str, text: str | None, names: dict[str, dict[int, str]]) -> str:
    """One stored value as the page would show it."""
    if text is None:
        return "nothing"
    value = changelog.decode(field, text)
    if field in names:
        return names[field].get(value, f"#{value}")
    if field == "amount":
        return money(value)
    if field in ("date_", "deleted_at"):
        return short_date(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _line(row: FinanceTransactionChangelog, names: dict[str, dict[int, str]]) -> str:
    label = changelog.LABELS.get(row.field, row.field)
    if row.field == "tag":
        added = row.new_value is not None
        tag = _said("tag", row.new_value if added else row.old_value, names)
        return f"Tag {tag} {'added' if added else 'removed'}"
    return f"{label}: {_said(row.field, row.old_value, names)} → {_said(row.field, row.new_value, names)}"


def _who(source: str) -> str:
    return _WHO.get(source) or source.replace("-", " ").title()


@router.get(CHANGES, include_in_schema=False)
async def changes(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    rows = await changelog.recent(service.db, owner_user_id)
    names = await _names(service.db, rows)
    txns = await transactions_by_ids(service.db, list({r.transaction_id for r in rows}))
    payees = {i: t.merchant_name or t.name for i, t in txns.items()}
    actions: dict[str, dict[str, Any]] = {}
    for row in rows:
        action = actions.setdefault(
            row.batch_id or f"row-{row.id}",
            {
                "batch": row.batch_id,
                "when": row.changed_at,
                "who": _who(row.change_source),
                "undone": row.undone_at is not None,
                "transactions": set(),
                "lines": [],
            },
        )
        action["transactions"].add(row.transaction_id)
        payee = payees.get(row.transaction_id, f"#{row.transaction_id}")
        action["lines"].append(f"{payee} · {_line(row, names)}")
    return render(
        request,
        "pages/settings/changes.html",
        {
            "section": SECTION,
            **nav_context("changes"),
            "actions": list(actions.values()),
            "undoable": await changelog.last_undoable(service.db, owner_user_id),
            "path": CHANGES,
        },
    )


@router.post(CHANGES + "/undo", include_in_schema=False)
async def undo(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Take back the newest action, and say what could not be."""
    put_back, left = await changelog.undo(service.db, owner_user_id)
    await service.db.commit()
    said = f"Undone: {put_back} change{'s' if put_back != 1 else ''} put back."
    if left:
        said += f" {left} had changed again since and were left."
    response = await changes(request, service, owner_user_id)
    return with_toast(response, said if put_back or left else "Nothing to undo.")


@router.get("/transactions/{transaction_id:int}/history", include_in_schema=False)
async def history(
    request: Request,
    transaction_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """One transaction's changes, newest first."""
    rows = [
        r
        for r in await changelog.history(service.db, transaction_id)
        if r.owner_user_id == (owner_user_id or 0)
    ]
    names = await _names(service.db, rows)
    return dialog(
        request,
        "partials/transactions/history.html",
        entries=[
            {
                "when": r.changed_at,
                "who": _who(r.change_source),
                "what": _line(r, names),
                "undone": r.undone_at is not None,
            }
            for r in rows
        ],
    )
