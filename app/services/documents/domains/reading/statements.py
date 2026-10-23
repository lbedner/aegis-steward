"""What a card statement says is due (#341).

A statement prints its balance, the day it is due and the least that
will do, on page one, the same way every month:

    New balance as of 09/07/26: $7,857.27
    Minimum payment due: $224.57
    Payment due date: 10/03/26

Read off one of the household's own cards, it is the card's latest
statement, kept where a provider would keep it, and a bill paying the
card in full expects it (``record_statement``). Not a proposal: these
are the card's own figures, as a sync would bring them, and the bill
they reach is the household's own.
"""

from __future__ import annotations

import re

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.figures import (
    Stated,
    labelled,
    money_in,
    stated_balance,
)
from app.services.documents.domains.reading.patterns import find_date

_DUE = re.compile(r"\bpayment due date\b", re.I)
_MINIMUM = re.compile(r"\bminimum payment due\b", re.I)


# What a statement calls the balance it closed at - not a letter's
# "current balance", which says nothing about a statement's date.
_CLOSING = re.compile(r"\b(new|ending|closing|statement) balance\b", re.I)


async def check_filed_statement(
    db: AsyncSession, document_id: int, stated: Stated | None = None
) -> int:
    """A filed statement's closing balance against the register on its
    printed date (#321), for any account it names by its last four; the
    gap, 0 for none. A paper that prints no closing balance, or no date
    beside it, is not a statement to check."""
    from app.services.finance.service import check_statement

    stated = stated or await stated_balance(db, document_id)
    if stated is None or stated.balance.as_of is None:
        return 0
    if not _CLOSING.search(stated.balance.because):
        return 0
    return await check_statement(
        db,
        stated.account,
        statement_date=stated.balance.as_of,
        printed=stated.balance.value_cents,
    )


async def record_card_statement(
    db: AsyncSession, document_id: int, stated: Stated | None = None
) -> bool:
    """Keep what a filed card statement says is due, when it is one of
    the household's cards; False for anything else."""
    from app.services.finance.service import record_statement

    # A balance AND a due date: what a card statement always prints, and
    # a letter that mentions a card's balance does not. Not its kind,
    # which is only settled once its metadata card is approved.
    stated = stated or await stated_balance(db, document_id)
    if stated is None or stated.account.account_type != "credit_card":
        return False
    read = stated.read
    due = next(filter(None, (find_date(t) for _p, _l, t in labelled(read, _DUE))), None)
    issued = stated.as_of
    if due is None or issued is None:
        return False
    minimum = next(
        filter(None, (money_in(t) for _p, _l, t in labelled(read, _MINIMUM))), None
    )
    return await record_statement(
        db,
        stated.account,
        balance=stated.balance.value_cents,
        issued=issued,
        due=due[0],
        minimum=minimum,
    )
