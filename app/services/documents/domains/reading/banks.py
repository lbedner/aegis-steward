"""Where an account is held, read off the paper its bank sends.

The reader filed a statement under its sender and onto its account and
stopped there, so the account stayed held with nobody - the Citizens
1098 did, and so did 15 of 19 accounts (#234). Both halves of the answer
were already in hand.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.identity import identify, known_strings
from app.services.documents.domains.reading.metadata import read_document

BANK = "account.institution"
# Paper a bank sends ABOUT an account it holds. A letter quoting the
# account is somebody asking about it - the county wanting statements -
# and that does not make them the bank.
BANK_PAPER = ("statement", "tax", "schedule")


async def propose_bank(
    db: AsyncSession, document_id: int, owner_user_id: int | None
) -> Any | None:
    """Where the account this paper is about is held, when nobody has
    said: the bank that sent a statement about it holds it.

    The Citizens 1098 was filed under Citizens and onto Citizens Bank
    Mortgage, and the account stayed held with nobody - 15 of 19 were
    (#234). The card is ``account.institution``, the one the assistant
    already proposes, so approving it runs the executor that exists.

    Never moves a bank: an account held somewhere was said by somebody.
    """
    from app.services.documents.domains.reading.proposals import (
        PROPOSED_BY,
        _already_asked,
        _letterhead,
        _read_pages,
        _sender,
    )
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.ledger.queries.accounts import account_by_id
    from app.services.finance.domains.writes.queue import propose

    document = await DocumentService(db).get(document_id)
    if document is None:
        return None
    read = await _read_pages(db, document_id)
    kind = next((f.value for f in read_document(read) if f.field == "kind"), None)
    if (kind or document.kind) not in BANK_PAPER:
        return None
    printed = identify(read, await known_strings(db))
    sender = await _sender(db, await _letterhead(db, read), printed)
    account_id = await _account_about(db, document_id, printed)
    if sender is None or account_id is None:
        return None
    account = await account_by_id(db, account_id)
    if account is None or account.institution_id is not None:
        return None
    if await _already_asked(db, BANK, account_id, key="account_id"):
        return None
    return await propose(
        db,
        BANK,
        {"account_id": account_id, "party_id": sender[1].id},
        owner_user_id=owner_user_id,
        proposed_by_agent=PROPOSED_BY,
    )


async def _account_about(
    db: AsyncSession, document_id: int, printed: list[Any]
) -> int | None:
    """The one account this paper is about: the last four it prints, or
    the account it is already filed on. Two filed accounts is no answer."""
    from app.services.documents.service import DocumentService
    from app.services.finance.constants import tagged_account

    for known, _finding in printed:
        if known.kind == "account":
            return int(known.target)
    filed = {
        found
        for tag in await DocumentService(db).tags_for(document_id)
        if (found := tagged_account(tag)) is not None
    }
    return filed.pop() if len(filed) == 1 else None
