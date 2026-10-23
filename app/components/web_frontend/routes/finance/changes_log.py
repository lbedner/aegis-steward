"""Every edit, in words, and the last one undone (#325).

Settings > Changes lists what was done, newest first, one action to a
line - who, when, on what - with Undo on the newest that can be taken
back. A register row's History is the same log for one transaction.
Values read as the page shows them: a payee by its name, money as money.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.filters import assistant, money, short_date
from app.components.web_frontend.nav import settings_nav as nav_context
from app.components.web_frontend.rendering import dialog, render, with_toast
from app.components.web_frontend.routes.finance.settings import SECTION
from app.core.formatting import payee_label
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import (
    FinanceService,
    FinanceTag,
    FinanceTransactionChangelog,
    account_names,
    category_names_by_id,
    changelog,
    merchants_by_ids,
    transactions_by_ids,
)

router = APIRouter()
CHANGES = f"{SECTION.path}/changes"

_WHO = {"you": "You", changelog.IMPORT: "An import", changelog.SYNC: "A sync"}
_WHO[changelog.UNDO] = "Undo"
_WHO[changelog.REDO] = "Redo"
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
    return _WHO.get(source) or assistant(source) or source


# Which change an action is named for, when it made several: the one a
# person did it for. Reviewed rides along a filing and goes unsaid.
_LEAD = (
    "category_id",
    "merchant_id",
    "tag",
    "memo",
    "deleted_at",
    "excluded_from_reports",
    "amount",
    "date_",
    "name",
    "account_id",
    "is_reviewed",
)


def _sentence(
    rows: list[FinanceTransactionChangelog],
    names: dict[str, dict[int, str]],
    payees: dict[int, str],
) -> str:
    """One action in words: what was done, to which charge (or how many)."""
    fields = {r.field: r for r in reversed(rows)}
    txn_ids = {r.transaction_id for r in rows}
    only = next(iter(txn_ids))
    what = (
        payees.get(only, "a charge") if len(txn_ids) == 1 else f"{len(txn_ids)} charges"
    )
    lead = next(f for f in _LEAD if f in fields)
    same = len({r.new_value for r in rows if r.field == lead}) == 1
    new = fields[lead].new_value
    to = _said(lead, new, names)
    said = {
        "category_id": f"Filed {what} under {to}"
        if same and new
        else f"Recategorized {what}",
        "merchant_id": f"Named the payee {to} on {what}"
        if same and new
        else f"Changed the payee on {what}",
        "tag": f"Tagged {what} \u201c{to}\u201d"
        if new
        else f"Took \u201c{_said('tag', fields[lead].old_value, names)}\u201d off {what}",
        "memo": f"Noted \u201c{to}\u201d on {what}"
        if same and new
        else f"Changed the note on {what}",
        "deleted_at": f"Removed {what}" if new else f"Restored {what}",
        "excluded_from_reports": f"Left {what} out of reports"
        if new == "true"
        else f"Counted {what} in reports again",
        "is_reviewed": f"Marked {what} reviewed"
        if new == "true"
        else f"Marked {what} not reviewed",
    }.get(lead, f"Edited the {changelog.LABELS.get(lead, lead).lower()} on {what}")
    others = len(fields.keys() - {lead, "is_reviewed"})
    if others:
        said += f", and {others} other change{'s' if others > 1 else ''}"
    return said


@router.get(CHANGES, include_in_schema=False)
async def changes(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    rows = await changelog.recent(service.db, owner_user_id)
    names = await _names(service.db, rows)
    txns = await transactions_by_ids(service.db, list({r.transaction_id for r in rows}))
    named = await merchants_by_ids(service.db, {t.merchant_id for t in txns.values()})
    # Called what the register calls it: the payee someone named first.
    payees = {
        i: payee_label(
            named[t.merchant_id].name if t.merchant_id in named else None,
            t.merchant_name,
            t.name,
        )
        for i, t in txns.items()
    }
    batches: dict[str, list[FinanceTransactionChangelog]] = {}
    for row in rows:
        # An undo or a redo marks the action it moved; it is not one.
        if row.change_source not in changelog.STEPS:
            batches.setdefault(row.batch_id or f"row-{row.id}", []).append(row)
    actions = [
        {
            "batch": batch[0].batch_id,
            "when": batch[0].changed_at,
            "who": _who(batch[0].change_source),
            "said": _sentence(batch, names, payees),
            "undone": batch[0].undone_at is not None,
            "lines": [
                f"{payees.get(r.transaction_id, '#' + str(r.transaction_id))} \u00b7 {_line(r, names)}"
                for r in batch
            ],
        }
        for batch in batches.values()
    ]
    return render(
        request,
        "pages/settings/changes.html",
        {
            "section": SECTION,
            **nav_context("changes"),
            "actions": actions,
            "undoable": await changelog.last_undoable(service.db, owner_user_id),
            "redoable": await changelog.last_redoable(service.db, owner_user_id),
            "path": CHANGES,
        },
    )


# Each step, and what it says it did.
_STEPS = {"undo": (changelog.undo, "Undone"), "redo": (changelog.redo, "Redone")}


@router.post(CHANGES + "/{step:str}", include_in_schema=False)
async def step(
    request: Request,
    step: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Undo the newest action, or redo the one undone last, and say what
    could not be moved."""
    move, done = _STEPS.get(step, (None, ""))
    if move is None:
        raise HTTPException(status_code=404)
    moved, left = await move(service.db, owner_user_id)
    await service.db.commit()
    said = f"{done}: {moved} change{'s' if moved != 1 else ''}."
    if left:
        said += f" {left} had changed again since and were left."
    response = await changes(request, service, owner_user_id)
    return with_toast(response, said if moved or left else f"Nothing to {step}.")


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
