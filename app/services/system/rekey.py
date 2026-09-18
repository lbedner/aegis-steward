"""Rotating the encryption key without losing what it protected.

Decryption uses exactly one key, so setting ``ENCRYPTION_KEY`` for the
first time orphans everything written under the ``SECRET_KEY`` fallback,
and you cannot re-encrypt what you cannot read. The rotation therefore
has to happen while BOTH keys are known: read each row with the old
material, write it back with the new.

Neither key is ever an argument. Both are read from the environment -
the new one as ``ENCRYPTION_KEY``, the old from wherever it came - so a
key never lands in a shell history.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.encryption import InvalidToken, decrypt_with, encrypt_secret
from app.core.log import logger


@dataclass(frozen=True)
class Encrypted:
    """One column holding a secret, and the AAD that binds it to its row.

    Registered rather than discovered, because the AAD is not derivable
    from the schema - but ``tests/services/test_rekey.py`` checks the
    registry against the models, so a new encrypted column cannot be
    quietly left out of a rotation.
    """

    table: str
    column: str
    model: Any
    context: Callable[[Any], str | None]


def _connection_context(column: str) -> Callable[[Any], str | None]:
    """The AAD a connection column was sealed with.

    ``access_token_encrypted`` is bound per PROVIDER rather than per row
    - one constant for Plaid, one for SnapTrade - while the rest follow
    the row-bound shape the finance constants document. The AAD has to
    match byte for byte, so this matches what the writers actually do
    rather than what the comment beside them says.
    """
    if column != "access_token_encrypted":
        return lambda row: f"finance_connection:{row.id}:{column}"
    return lambda row: (
        "finance.snaptrade.user_secret"
        if row.provider == "snaptrade"
        else "finance.plaid.access_token"
    )


def _secrets() -> tuple[Encrypted, ...]:
    """The registry, bound at call time so importing this module does
    not drag every service in with it."""
    from app.services.finance.constants import ENCRYPTED_COLUMNS
    from app.services.finance.models import FinanceAccount, FinanceConnection
    from app.services.matters.models import SignIn

    return (
        Encrypted(
            table="sign_in",
            column="secret_encrypted",
            model=SignIn,
            context=lambda row: f"sign_in:{row.id}:secret",
        ),
        Encrypted(
            table="finance_account",
            column="account_number_encrypted",
            model=FinanceAccount,
            context=lambda row: f"finance_account:{row.id}:number",
        ),
        *(
            Encrypted(
                table="finance_connection",
                column=column,
                model=FinanceConnection,
                context=_connection_context(column),
            )
            # The finance service already keeps this list, written down
            # so "key-rotation tooling can find every finance secret".
            # Taking it from there rather than naming them again is what
            # stops a sixth column being added and quietly skipped.
            for column in ENCRYPTED_COLUMNS
            if hasattr(FinanceConnection, column)
        ),
    )


SECRETS = _secrets()


async def rekey_secrets(db: AsyncSession, *, old_key_material: bytes) -> dict[str, int]:
    """Re-encrypt every stored secret under the key now in force.

    Returns how many rows moved, per column. A row the OLD key cannot
    read is left exactly as it is: that is what an already-rotated row
    looks like, so a second run is safe rather than double-sealing it.
    """
    moved: dict[str, int] = {}
    for one in SECRETS:
        column = getattr(one.model, one.column)
        rows = (await db.exec(select(one.model).where(col(column).is_not(None)))).all()
        count = 0
        for row in rows:
            sealed = getattr(row, one.column)
            if not sealed:
                continue
            aad = one.context(row)
            try:
                plain = decrypt_with(sealed, key_material=old_key_material, context=aad)
            except (InvalidToken, Exception):  # noqa: B014
                # Already under the new key, or unreadable by either.
                continue
            setattr(row, one.column, encrypt_secret(plain, context=aad))
            db.add(row)
            count += 1
        moved[f"{one.table}.{one.column}"] = count
        logger.info("Re-keyed %s rows of %s.%s", count, one.table, one.column)
    await db.flush()
    return moved
