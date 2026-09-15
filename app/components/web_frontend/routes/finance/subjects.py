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
    """The filter, drawn only when there is something to filter.

    A household with nobody else's money in it never sees this: a
    control whose only option is the one you are already looking at is
    noise on every page it appears on.
    """
    subjects = await service.list_subjects()
    if not subjects:
        return {"whose_options": [], "whose": OURS}
    chosen = current if current in (OURS, ALL) else (current or OURS)
    return {
        "whose_options": [
            {"key": OURS, "label": "Ours"},
            *[
                {"key": str(subject.id), "label": subject.name}
                for subject in subjects
            ],
            {"key": ALL, "label": "Everyone"},
        ],
        "whose": chosen,
    }
