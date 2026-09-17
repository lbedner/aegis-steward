"""The numbers a bank knows you by.

Two of them, kept differently on purpose. A routing number is printed on
every cheque, so it is plain and only wants checking. An account number
with a routing number beside it is enough for somebody to pull a debit,
so it is kept the way a sign-in's password is: encrypted at rest, never
in a listing, revealed into one row on request.
"""

from __future__ import annotations

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import decrypt_secret, encrypt_secret

# The ABA weights, oldest trick in banking: a transposed pair fails the
# sum, so a mistyped routing number is caught before it is filed.
_ABA_WEIGHTS = (3, 7, 1, 3, 7, 1, 3, 7, 1)
MASK_DIGITS = 4


def _digits(raw: str | None) -> str:
    return "".join(one for one in (raw or "") if one.isdigit())


def aba_ok(routing: str | None) -> bool:
    """Whether this could be a routing number at all. Spacing and dashes
    are how people write one down, so they do not decide it."""
    if not routing or len(_digits(routing)) != 9 or len(routing.strip()) < 9:
        return False
    if any(one.isalpha() for one in routing):
        return False
    numbers = _digits(routing)
    total = sum(weight * int(one) for weight, one in zip(_ABA_WEIGHTS, numbers))
    return total % 10 == 0


def _context(account_id: int) -> str:
    """AAD binding the ciphertext to its row."""
    return f"finance_account:{account_id}:number"


async def set_number(db: AsyncSession, account_id: int, number: str | None) -> Any:
    """Store an account's own number and derive its mask.

    A blank leaves the stored number alone. It can never mean "clear
    it": the form never showed the old one, so an empty box is a field
    nobody touched, and reading it as a deletion loses a number to an
    edit of the name.
    """
    from app.services.finance.domains.ledger.queries.accounts import account_by_id

    account = await account_by_id(db, account_id)
    if account is None:
        raise ValueError(f"Account {account_id} not found.")
    clean = (number or "").strip()
    if not clean:
        return account
    account.account_number_encrypted = encrypt_secret(
        clean, context=_context(account_id)
    )
    # Derived, never typed twice: the masked display then cannot
    # disagree with the number it is masking.
    account.mask = _digits(clean)[-MASK_DIGITS:] or None
    db.add(account)
    await db.flush()
    return account


async def reveal_number(db: AsyncSession, account_id: int) -> str | None:
    """The number itself, asked for on purpose. Never part of a listing.

    A key that has been rotated leaves the ciphertext unreadable, which
    reads as "no number stored" if it is swallowed - so this lets the
    failure out.
    """
    from app.services.finance.domains.ledger.queries.accounts import account_by_id

    account = await account_by_id(db, account_id)
    if account is None or not account.account_number_encrypted:
        return None
    return decrypt_secret(
        account.account_number_encrypted, context=_context(account_id)
    )
