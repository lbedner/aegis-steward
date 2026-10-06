"""Which of your accounts a newly linked one is (#309).

A link beside Quicken exports can report an account the exports already
feed. One that matches exactly attaches on its own; one that could be
several waits on its connection (``connections.placing``) until you say:
one of yours, or an account of its own. Then the worker syncs the
connection from the start and the history it held arrives.

Its own module because Settings is at its budget.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.components.web_frontend.filters import as_options
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, or_404
from app.services.finance.adapters.providers import connections
from app.services.finance.adapters.providers.connections import placing
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.models import FinanceConnection
from app.services.finance.service import FinanceService

SECTION = section("settings")
router = APIRouter(prefix=SECTION.path)

FIELD = "place-"
# The answers that are not one of yours: in the select, and in sight
# beside it, since they are the ones people hesitate over.
WAYS_OUT = [(placing.OWN, "A new account"), (placing.SKIP, "Skip for now")]
# The recent charges a step shows: the ones it was matched by first.
CHARGES_SHOWN = 4
NOTHING_LEFT = "Nothing left to place."


async def first_waiting(
    service: FinanceService, owner_user_id: int | None, connection_ids: Iterable[int]
) -> FinanceConnection | None:
    """The first of ``connection_ids`` - the ones a connect just made -
    holding accounts for you to place. Another bank's wait their turn on
    its card."""
    for connection_id in connection_ids:
        row = await connections.get_connection(
            service.db, connection_id, owner_user_id=owner_user_id
        )
        if row is not None and placing.unplaced(row):
            return row
    return None


async def place_dialog(
    request: Request,
    service: FinanceService,
    connection: FinanceConnection,
    *,
    answers: dict[str, str] | None = None,
    errors: list[str] | None = None,
    offered: placing.Choices | None = None,
) -> Response:
    """The choice, one select per held account (``offered``, when the
    caller has read them)."""
    if offered is None:
        offered = await placing.choices(service.db, connection)
    picks = await placing.suggestions(service.db, offered)
    logos = await _bank_logos(service, [held for held, _yours in offered])
    rows: list[dict[str, Any]] = []
    for held, yours in offered:
        pick = picks.get(held["id"])
        matched = set(pick.matched) if pick else set()
        charges = sorted(
            enumerate(held.get("charges") or []), key=lambda c: c[0] not in matched
        )[:CHARGES_SHOWN]
        rows.append(
            {
                "held": held,
                "field": FIELD + held["id"],
                "options": as_options([(str(a.id), a.name) for a in yours] + WAYS_OUT),
                "charges": [(*charge, i in matched) for i, charge in charges],
                # What you answered beats what is suggested.
                "chosen": (answers or {}).get(held["id"])
                or (str(pick.account_id) if pick else None),
                "reason": pick.reason if pick else None,
                "logo": logos.get(held.get("bank") or ""),
            }
        )
    return dialog(
        request,
        "partials/settings/place.html",
        status_code=422 if errors else 200,
        connection=connection,
        rows=rows,
        ways_out=WAYS_OUT,
        errors=errors or [],
        path=SECTION.path,
    )


async def _bank_logos(
    service: FinanceService, held: list[dict[str, Any]]
) -> dict[str, str]:
    """``{bank name: logo url}`` for the banks behind the held accounts, the
    way an account's bank gets its mark: the bank's own domain beats a
    guess from its name. One not fetched yet is asked for and shows next
    time; until then the step shows the bank's initial."""
    from app.services.finance.domains.ledger import merchant_icon

    banks = {h["bank"]: h.get("bank_domain") for h in held if h.get("bank")}
    keys = await merchant_icon.resolve_icon_keys(
        service.db, list(banks), {bank: d for bank, d in banks.items() if d}
    )
    return {bank: merchant_icon.icon_url(key) for bank, key in keys.items()}


async def _connection(
    service: FinanceService, owner_user_id: int | None, connection_id: int
) -> FinanceConnection:
    return or_404(
        await connections.get_connection(
            service.db, connection_id, owner_user_id=owner_user_id
        )
    )


@router.get("/connections/{connection_id:int}/place", include_in_schema=False)
async def place_form(
    request: Request,
    connection_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    connection = await _connection(service, owner_user_id, connection_id)
    if not placing.unplaced(connection):
        return dialog_done(SECTION.path, NOTHING_LEFT)
    return await place_dialog(request, service, connection)


@router.post("/connections/{connection_id:int}/place", include_in_schema=False)
async def place(
    request: Request,
    connection_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Put each held account where you said; the worker then pulls its
    history - the bank's whole history again, which is no request's job."""
    connection = await _connection(service, owner_user_id, connection_id)
    if not placing.unplaced(connection):  # a second submit, a stale form
        return dialog_done(SECTION.path, NOTHING_LEFT)
    answers = {
        key.removeprefix(FIELD): str(value)
        for key, value in (await request.form()).items()
        if key.startswith(FIELD)
    }
    offered = await placing.choices(service.db, connection)
    errors = await placing.place(service.db, connection, answers, offered)
    if errors:
        return await place_dialog(
            request,
            service,
            connection,
            answers=answers,
            errors=errors,
            offered=offered,
        )
    await service.db.commit()
    if set(answers.values()) <= {placing.SKIP}:
        return dialog_done(SECTION.path, "Skipped for now; they wait on the card.")
    await placing.sync_soon(connection)
    return dialog_done(SECTION.path, "Placed. Its history is on its way.")
