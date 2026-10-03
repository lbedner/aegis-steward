"""Deleting an account, or a bank's data, permanently (#372).

"Remove account" hides an account and keeps every row; testing against a
demo bank put fake history in a real household's database with no way to
take it out. A purge takes the account and everything that hangs off it,
and nothing else. The check walks every foreign key in the schema, so a
table added later that points at an account or a transaction is covered
without this test knowing its name.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.model_registry import import_all_models
from app.services.finance.domains.ledger import links
from app.services.finance.models import (
    FinanceAccount,
    FinanceBalanceSnapshot,
    FinanceTag,
    FinanceTransaction,
    FinanceTransactionSplit,
    FinanceTransactionTag,
    FinanceValuation,
)
from app.services.finance.service import FinanceService

import_all_models()


async def _referring(
    db: AsyncSession, account_ids: set[int], transaction_ids: set[int]
) -> list[str]:
    """Every row, in any table, still pointing at these accounts or
    transactions: ``table.column`` per row found."""
    found: list[str] = []
    for table in SQLModel.metadata.sorted_tables:
        for fk in table.foreign_keys:
            target = fk.column.table.name
            ids = (
                account_ids
                if target == "finance_account"
                else transaction_ids
                if target == "finance_transaction"
                else None
            )
            if not ids:
                continue
            count = (
                await db.exec(
                    select(func.count()).select_from(table).where(fk.parent.in_(ids))
                )
            ).one()
            found += [f"{table.name}.{fk.parent.name}"] * int(count)
    return found


@pytest.mark.asyncio
async def test_an_account_and_its_history_go_and_nothing_else(
    async_db_session: AsyncSession,
) -> None:
    db = async_db_session
    finance = FinanceService(db)
    demo = await finance.create_manual_account(
        name="Demo Checking", account_type="checking", classification="asset"
    )
    ours = await finance.create_manual_account(
        name="Our Checking", account_type="checking", classification="asset"
    )
    doomed = [
        await finance.create_transaction(
            account_id=demo.id, amount=-cents, txn_date=date(2026, 9, 1), name=name
        )
        for cents, name in ((1550, "Bait"), (12550, "Groceries"))
    ]
    kept = await finance.create_transaction(
        account_id=ours.id, amount=1550, txn_date=date(2026, 9, 1), name="Transfer in"
    )
    # Ours points at one of theirs (a paired transfer): the pointer clears,
    # our row stays.
    kept.transfer_pair_transaction_id = doomed[0].id
    tag = FinanceTag(owner_user_id=0, name="Fishing", normalized_name="fishing")
    db.add(tag)
    await db.flush()
    db.add_all(
        [
            FinanceTransactionSplit(parent_transaction_id=doomed[1].id, amount=-500),
            FinanceTransactionTag(transaction_id=doomed[0].id, tag_id=tag.id),
            FinanceBalanceSnapshot(
                account_id=demo.id, balance_date=date(2026, 9, 1), balance=100
            ),
            FinanceValuation(account_id=demo.id, as_of_date=date(2026, 9, 1), value=1),
            kept,
        ]
    )
    await links.link(db, doomed[1].id, "matter:1")
    await db.commit()
    doomed_ids = {t.id for t in doomed}

    removed = await finance.purge_account(demo.id)
    await db.commit()

    assert removed == 2
    assert await _referring(db, {demo.id}, doomed_ids) == []
    assert await db.get(FinanceAccount, demo.id) is None
    survivor = (
        await db.exec(
            select(FinanceTransaction).where(FinanceTransaction.id == kept.id)
        )
    ).one()
    assert survivor.transfer_pair_transaction_id is None
    assert await db.get(FinanceAccount, ours.id) is not None
    assert await db.get(FinanceTag, tag.id) is not None  # the tag is the user's


@pytest.mark.asyncio
async def test_an_account_that_is_not_there_purges_nothing(
    async_db_session: AsyncSession,
) -> None:
    assert await FinanceService(async_db_session).purge_account(999_999) is None
