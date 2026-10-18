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
    account_on,
    balances,
    document_row,
    labelled,
    money_in,
)
from app.services.documents.domains.reading.findings import Page
from app.services.documents.domains.reading.patterns import find_date

_DUE = re.compile(r"\bpayment due date\b", re.I)
_MINIMUM = re.compile(r"\bminimum payment due\b", re.I)


async def record_card_statement(db: AsyncSession, document_id: int) -> bool:
    """Keep what a filed card statement says is due, when it is one of
    the household's cards; False for anything else."""
    from app.services.documents.queries import pages_for
    from app.services.finance.domains.planning.recurring.statements import (
        record_statement,
    )

    read: list[Page] = [
        {"page": page.page_number, "text": page.text}
        for page in await pages_for(db, document_id)
    ]
    # A balance AND a due date: what a card statement always prints, and
    # a letter that mentions a card's balance does not. Not its kind,
    # which is only settled once its metadata card is approved.
    found = balances(read)
    due = next(filter(None, (find_date(t) for _p, _l, t in labelled(read, _DUE))), None)
    if not found or due is None:
        return False
    card = await account_on(db, read)
    if card is None or card.account_type != "credit_card":
        return False
    document = await document_row(db, document_id)
    issued = found[0].as_of or (document.document_date if document else None)
    if issued is None:
        return False
    minimum = next(
        filter(None, (money_in(t) for _p, _l, t in labelled(read, _MINIMUM))), None
    )
    return await record_statement(
        db,
        card,
        balance=found[0].value_cents,
        issued=issued,
        due=due[0],
        minimum=minimum,
    )
