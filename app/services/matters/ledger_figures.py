"""What the register says, for an ask that wants a figure we have not proved.

The county asked for the balance as of 1 August 2026. The register held
762 imported transactions summing to $3,137.44 and no statement covered
that date, so the answer sheet said "nothing filed against this yet"
while the app plainly knew a number (2026-09-18).

Both halves of that are wrong to hide. The number is worth seeing - it
is what the answer will be, near enough to plan around - and the gap
between what the register BELIEVES and what a statement PROVES is worth
seeing more, because it is the thing somebody has to go and close before
the form can be sent. So: offered, never counted. An item with only this
against it stays missing.

Nothing is guessed. The ask has to NAME an attribute this can answer and
a date to answer it ON; a running total with no date on it is not an
answer to anything a county asked.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

# What the register can speak to. A balance and a resource value are the
# same question asked by two agencies; income is NOT here, because a
# month's deposits are not a wage and an app that offered them as one
# would be inventing a figure somebody then writes on a form.
FROM_THE_REGISTER = ("account_balance", "resource_value")


async def unproven_figures(
    db: AsyncSession, matter_id: int, item: Any
) -> list[dict[str, Any]]:
    """What the register says about this ask, per account, as of its date.

    Empty unless the ask names an attribute the register can speak to
    AND the date it is asked as of. Empty, too, when the matter names no
    subject: whose accounts these are is the whole question, and every
    account in the app is not an answer to it.
    """
    from app.services.finance.domains.ledger import queries
    from app.services.finance.domains.ledger.accounts import register_balance_as_of
    from app.services.matters.matters import MatterService

    if item.ask not in FROM_THE_REGISTER or item.as_of is None:
        return []
    matter = await MatterService(db).get(matter_id)
    if matter is None or matter.subject_party_id is None:
        return []

    offers = []
    for account in await _accounts_of(db, matter.subject_party_id):
        # An account whose register holds nothing has nothing to say,
        # and a "$0.00, unverified" line beside an ask is worse than
        # silence: it reads as an answer somebody checked.
        if not await queries.has_nonreconcile_register(db, account.id):
            continue
        offers.append(
            {
                "account_id": account.id,
                "account": account.name,
                "value_cents": await register_balance_as_of(db, account.id, item.as_of),
                "as_of": item.as_of,
                # Said outright rather than left to a colour: this sheet
                # is printed and read beside a form in black and white.
                "proven": False,
            }
        )
    return offers


async def _accounts_of(db: AsyncSession, party_id: int) -> list[Any]:
    """The accounts held in this party's name.

    Both halves are already written: ``subject_of`` is the read-only
    party-to-subject lookup (a page that looks at a contact must not make
    them a subject by looking), and ``accounts_page`` is the one query
    that knows what "whose accounts" means - including that a hidden or
    deleted row is not one.
    """
    from app.services.finance.domains.ledger import queries
    from app.services.finance.domains.ledger.subjects import subject_of

    subject = await subject_of(db, party_id)
    if subject is None:
        return []
    accounts, _total = await queries.accounts_page(
        db,
        owner_user_id=None,
        include_hidden=False,
        page=1,
        page_size=200,
        subject_id=subject.id,
    )
    return [account for account in accounts if not account.is_closed]
