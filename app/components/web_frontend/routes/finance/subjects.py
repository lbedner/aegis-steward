"""Whose money an account holds.

One home for the question, because it is asked in three places - the
add-account dialog, the Accounts filter, and every listing that has to
decide whether somebody else's pension belongs in our total - and three
answers would drift the day one of them changed.

A subject is the money side of a party: James Bedner the person in the
address book, and James Bedner whose pension account this is, are one
identity. The subject row is made when an account is first put in
somebody's name rather than maintained as a second list.
"""

from __future__ import annotations

from typing import Any

from app.services.finance.domains.ledger.queries.accounts import EVERYONE, HOUSEHOLD
from app.services.finance.domains.ledger.subjects import subject_filter
from app.services.finance.service import FinanceService

__all__ = ["EVERYONE", "HOUSEHOLD", "chips", "people", "in_someone_elses_name", "whose"]

# What the URL says. ``?whose=ours`` is the default and the household's
# own; ``?whose=all`` is everybody's; a number is one subject.
OURS = "ours"
ALL = "all"


async def people(service: FinanceService) -> list[dict[str, Any]]:
    """Who an account can belong to. People only: a pension belongs to a
    person, and the agency that pays it is not a candidate."""
    from app.services.matters.service import PartyService

    return [
        {"id": party.id, "name": party.name}
        for party in await PartyService(service.db).find(kind="person")
    ]


async def in_someone_elses_name(
    service: FinanceService,
    account_id: int,
    party_id: int,
    owner_user_id: int | None = None,
) -> None:
    """Point an account at the person whose money it holds."""
    from app.services.finance.domains.ledger.subjects import subject_for_party
    from app.services.matters.service import PartyService

    party = await PartyService(service.db).get(party_id)
    if party is None:
        return
    subject = await subject_for_party(
        service.db, party_id, name=party.name, owner_user_id=owner_user_id
    )
    await service.assign_subject(account_id, subject.id, owner_user_id=owner_user_id)


def whose(value: str | None) -> int | None:
    """The ``?whose=`` parameter as a subject filter.

    The ledger's own parser, not a second reading of it: a URL, a tool
    argument and a form all ask this question, and three answers is how
    one of them starts counting a parent's pension as ours.
    """
    return subject_filter(value)


async def chips(service: FinanceService, current: str | None) -> dict[str, Any]:
    """The filter, drawn only when there is something to filter, plus
    the line that says what is being held out of the total.

    A household with nobody else's money in it never sees any of this: a
    control whose only option is the one you are already looking at is
    noise on every page it appears on.

    The line exists because "held but not counted" is invisible by
    construction. Approving an account for a parent and then finding the
    portfolio unchanged reads as nothing having happened - which is
    exactly what it looked like the first time this shipped.
    """
    subjects = await service.list_subjects()
    if not subjects:
        return {"whose_options": [], "whose": OURS, "also_held": []}
    chosen = current if current in (OURS, ALL) else (current or OURS)
    held = []
    for subject in subjects:
        rows, total = await service.list_accounts(page_size=1, subject_id=subject.id)
        if total:
            held.append(
                {
                    "key": str(subject.id),
                    "name": subject.name,
                    "count": total,
                    "one": rows[0].name if total == 1 else "",
                }
            )
    return {
        "whose_options": [
            {"key": OURS, "label": "Ours"},
            *[{"key": str(subject.id), "label": subject.name} for subject in subjects],
            {"key": ALL, "label": "Everyone"},
        ],
        "whose": chosen,
        # Only under "ours": on their own page the whole list is theirs.
        "also_held": held if chosen == OURS else [],
    }


async def whose_name(service: FinanceService, subject_id: int | None) -> str | None:
    """Whose money this account holds, by name, or None for ours.

    On the portfolio the chip says it; inside one account there is
    nothing else on the page that does, and an account read as ours
    when it is a parent's is the mistake this whole flag exists to
    prevent.
    """
    if not subject_id:
        return None
    for subject in await service.list_subjects():
        if subject.id == subject_id:
            return subject.name
    return None


async def who_and_where(
    service: FinanceService, account: Any, owner_user_id: int | None = None
) -> dict[str, Any]:
    """The people and places behind an account.

    An account with no transactions is not an account with nothing to
    say. Whose money it is and who it is held with are both rows in the
    address book, carrying an address, a phone and a website that are
    exactly what somebody reaches for when they have to ring the pension
    fund - and they were a click away behind two dialogs, on a page that
    looked empty.

    Nothing at all when the account has neither: an empty card on every
    ordinary account teaches people to skip the space it sits in.
    """
    from app.services.matters.service import PartyService
    from app.services.matters.signins import SignInService, drawn

    parties = PartyService(service.db)
    cards: list[dict[str, Any]] = []
    subject_party_id: int | None = None
    if account is not None and account.subject_id:
        for subject in await service.list_subjects():
            if subject.id == account.subject_id and subject.party_id:
                subject_party_id = subject.party_id
    held_party_id: int | None = None
    routing: str | None = None
    if account is not None and account.institution_id:
        for bank in await service.list_institutions(owner_user_id=owner_user_id):
            if bank.id == account.institution_id:
                held_party_id = bank.party_id
                routing = bank.routing_number
    for role, party_id in (
        ("Whose money", subject_party_id),
        ("Held with", held_party_id),
    ):
        if not party_id:
            continue
        party = await parties.get(party_id)
        if party is None:
            continue
        # Only under the place: a login is how you get INTO somewhere,
        # and listing it under its owner too says one thing twice on a
        # page that is already answering one question.
        rows = (
            await SignInService(service.db).at_site(party_id)
            if role == "Held with"
            else []
        )
        cards.append(
            {
                "role": role,
                "party_id": party.id,
                "name": party.name,
                "contact": {
                    key: value for key, value in (party.contact or {}).items() if value
                },
                # The way IN, named but never opened here: the password
                # lives one deliberate click away, on the party.
                "signins": [drawn(one) for one in rows],
                # The bank's own number, printed on every cheque, and
                # only ever under the bank: it belongs to them, not to
                # the account looking at them.
                "routing": routing if role == "Held with" else None,
                "note": party.note or "",
            }
        )
    return {"who_and_where": cards}
