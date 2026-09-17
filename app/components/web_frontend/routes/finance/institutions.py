"""Where an account is held.

Its own module because the answer has two directories behind it: the
ledger's institutions - which carry a logo, a provider id and the
capability flags a connection gates on - and the address book, which
carries who an organization IS. A pension fund is already a party;
naming it here would otherwise type it a second time and leave the two
drifting.

Split out of ``account_manage`` at the budget, which is what having one
is for.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import dialog, dialog_done, or_404
from app.components.web_frontend.routes.finance.account_manage import _account
from app.components.web_frontend.routes.finance.transactions import picker_options
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path)


@router.get("/{account_id:int}/institution", include_in_schema=False)
async def institution_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The institution an account is held at. Opens on the one it
    already has, or on
    the one most recently set anywhere - three brokerage accounts at one
    bank should cost one decision, not three."""
    account = await _account(service, account_id, owner_user_id)
    return dialog(
        request,
        "partials/accounts/institution.html",
        account=account,
        institutions=await _held_with_options(service, owner_user_id),
        routing=await _routing_now(service, account.institution_id),
        current=_institution_current(
            account.institution_id
            or await service.last_institution_used(owner_user_id=owner_user_id)
        ),
        suggestion=None
        if account.institution_id
        else await _suggested_bank(service, account.name, owner_user_id),
        errors=[],
    )


async def _refused(
    request: Request,
    service: FinanceService,
    account_id: int,
    owner_user_id: int | None,
    routing: str,
) -> Response:
    """The form back with what refused it: a routing number whose
    checksum fails is usually a transposed pair, not a new bank."""
    account = await _account(service, account_id, owner_user_id)
    return dialog(
        request,
        "partials/accounts/institution.html",
        status_code=422,
        account=account,
        institutions=await _held_with_options(service, owner_user_id),
        routing=routing,
        current=_institution_current(account.institution_id),
        suggestion=None,
        errors=["That is not a routing number; check for a transposed pair."],
    )


async def _routing_now(service: FinanceService, institution_id: int | None) -> str:
    """The bank's routing number as the form should open on it."""
    from app.services.finance.domains.ledger.queries.accounts import institution_by_id

    if institution_id is None:
        return ""
    found = await institution_by_id(service.db, institution_id)
    return (found.routing_number or "") if found else ""


async def _suggested_bank(
    service: FinanceService, account_name: str, owner_user_id: int | None
) -> str | None:
    """A payee whose name appears IN the account's, or None.

    An account called TOTAL CHECKING (CHASE) says who it is with, and the
    ledger already knows Chase with a working logo - so offer it rather
    than asking someone to type what is on the screen. A whole-word match
    on a payee that actually exists, never a guess: ROTH IRA suggests
    nothing, which is right, because that account's bank is not in its
    name.
    """
    import re

    from app.services.finance.utils import normalize_payee

    haystack = normalize_payee(account_name)
    if not haystack:
        return None
    payees = await service.list_merchants(owner_user_id=owner_user_id)
    best: str | None = None
    for payee in sorted(payees, key=lambda p: -len(p.normalized_name or "")):
        needle = payee.normalized_name or ""
        if len(needle) >= 3 and re.search(rf"\b{re.escape(needle)}\b", haystack):
            best = payee.name
            break
    return best


async def _held_with_options(
    service: FinanceService, owner_user_id: int | None
) -> list[Any]:
    """Banks the ledger knows, plus organizations the address book does.

    A pension fund is already a party - with its website, its sign-in and
    its part in a matter - and typing it again here would make a second
    row of one body. Party options submit as ``party:<id>`` so the save
    can tell which directory the answer came from; an organization that
    already has a ledger row is offered once, as the row.
    """
    from types import SimpleNamespace

    from app.services.matters.service import PartyService

    banks = await service.list_institutions(owner_user_id=owner_user_id)
    linked = {bank.party_id for bank in banks if bank.party_id}
    options = list(picker_options(banks))
    options.extend(
        SimpleNamespace(id=f"party:{party.id}", name=party.name, fact="")
        for party in await PartyService(service.db).find(kind="organization")
        if party.id not in linked
    )
    return sorted(options, key=lambda option: option.name.lower())


def _institution_current(institution_id: int | None) -> set[int]:
    """What the picker marks as already chosen."""
    return {institution_id} if institution_id else set()


@router.post("/{account_id:int}/institution", include_in_schema=False)
async def institution_save(
    request: Request,
    account_id: int,
    institution_id: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
    routing_number: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Pick one, name a new one, or clear it.

    A typed name is created here rather than anywhere else, so naming a
    bank and setting it are one action. The domain is left to the icon
    resolver's guess unless a homepage is given later: on a real ledger
    fidelity.com, chase.com and citi.com all resolve from the name alone.
    """
    account = await _account(service, account_id, owner_user_id)
    label = new_name.strip()
    chosen: int | None = None
    if label:
        chosen = (
            await service.get_or_create_institution(
                name=label, owner_user_id=owner_user_id
            )
        ).id
    elif institution_id.startswith("party:"):
        # An organization the address book already holds: the ledger row
        # is made from it and points back, so the two cannot drift.
        chosen = (await _from_party(service, int(institution_id[6:]))).id
    elif institution_id:
        chosen = int(institution_id)
    await service.set_account_institution(
        account_id, chosen, owner_user_id=owner_user_id
    )
    # The routing number rides with the BANK, so it is written once here
    # however many accounts point at it. A blank leaves it alone.
    if chosen is not None and routing_number.strip():
        from app.services.finance.domains.ledger.numbers import aba_ok
        from app.services.finance.domains.ledger.queries.accounts import (
            institution_by_id,
        )

        if not aba_ok(routing_number):
            return await _refused(
                request, service, account_id, owner_user_id, routing_number
            )
        bank = await institution_by_id(service.db, chosen)
        if bank is not None:
            bank.routing_number = routing_number.strip()
            service.db.add(bank)
    await service.db.commit()
    named = (
        label
        or next(
            (
                i.name
                for i in await service.list_institutions(owner_user_id=owner_user_id)
                if i.id == chosen
            ),
            "",
        )
        if chosen
        else ""
    )
    return dialog_done(
        f"{SECTION.path}/{account_id}",
        f"{account.name} is held at {named}"
        if named
        else f"{account.name} has no institution",
    )


async def _from_party(service: FinanceService, party_id: int) -> Any:
    """The ledger's institution row for a party, made if it is new."""
    from app.services.finance.domains.ledger.subjects import institution_for_party
    from app.services.matters.service import PartyService

    party = await PartyService(service.db).get(party_id)
    or_404(party)
    return await institution_for_party(
        service.db,
        party_id,
        name=party.name,
        website=str((party.contact or {}).get("website") or "") or None,
    )
