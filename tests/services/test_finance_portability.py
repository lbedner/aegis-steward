"""A ledger out of one instance and into another, whole.

A statement import carries transactions; everything the ledger becomes
around them lives only in the database. These cover the round trip that
moves it.
"""

from __future__ import annotations

from datetime import date
import gzip
import json
from pathlib import Path

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance import portability
from app.services.finance.models import (
    FinanceAccount,
    FinanceCategory,
    FinanceTransaction,
)
from app.services.finance.service import FinanceService


async def _ledger(session: AsyncSession) -> tuple[int, int]:
    """A small ledger with the shapes that make a restore hard: a child
    category under a parent, and two transactions pointing at each other
    as a transfer pair."""
    service = FinanceService(session)
    checking = await service.create_manual_account(
        owner_user_id=None,
        name="CLASSIC CHECKING",
        account_type="checking",
        classification="asset",
    )
    card = await service.create_manual_account(
        owner_user_id=None,
        name="AMEX",
        account_type="credit_card",
        classification="liability",
    )
    await session.flush()

    parent = FinanceCategory(
        name="Utilities", slug="utilities", classification="expense"
    )
    session.add(parent)
    await session.flush()
    session.add(
        FinanceCategory(
            name="Water", slug="water", classification="expense", parent_id=parent.id
        )
    )

    out = FinanceTransaction(
        account_id=checking.id,
        date_=date(2026, 7, 1),
        amount=-20000,
        name="Payment",
        source="manual",
    )
    into = FinanceTransaction(
        account_id=card.id,
        date_=date(2026, 7, 1),
        amount=20000,
        name="Payment",
        source="manual",
    )
    session.add(out)
    session.add(into)
    await session.flush()
    # The link no insert order can satisfy: each row names the other.
    out.transfer_pair_transaction_id = into.id
    into.transfer_pair_transaction_id = out.id
    await session.commit()
    return out.id or 0, into.id or 0


@pytest.mark.asyncio
async def test_a_round_trip_brings_the_whole_ledger_back(
    async_db_session: AsyncSession, tmp_path: Path
) -> None:
    out_id, into_id = await _ledger(async_db_session)
    archive = tmp_path / "finance.jsonl.gz"

    written = await portability.export_finance(async_db_session, archive)
    assert written["finance_account"] == 2
    assert written["finance_transaction"] == 2
    assert written["finance_category"] == 2

    read, repairs = await portability.restore_finance(
        async_db_session, archive, replace=True
    )
    assert read["finance_account"] == 2
    assert repairs == {}

    accounts = (await async_db_session.exec(select(FinanceAccount))).all()
    assert {a.name for a in accounts} == {"CLASSIC CHECKING", "AMEX"}
    # Keys survive, so what points at what still means something.
    pair = (
        await async_db_session.exec(
            select(FinanceTransaction).where(FinanceTransaction.id == out_id)
        )
    ).one()
    assert pair.transfer_pair_transaction_id == into_id
    child = (
        await async_db_session.exec(
            select(FinanceCategory).where(FinanceCategory.name == "Water")
        )
    ).one()
    parent = (
        await async_db_session.exec(
            select(FinanceCategory).where(FinanceCategory.name == "Utilities")
        )
    ).one()
    assert child.parent_id == parent.id


@pytest.mark.asyncio
async def test_the_archive_names_itself(
    async_db_session: AsyncSession, tmp_path: Path
) -> None:
    """A restore reads the header before it deletes anything."""
    archive = tmp_path / "finance.jsonl.gz"
    await portability.export_finance(async_db_session, archive)
    header = portability.read_header(archive)
    assert header["format"] == portability.FORMAT
    assert "finance_account" in header["tables"]

    stranger = tmp_path / "stranger.jsonl.gz"
    with gzip.open(stranger, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"format": "something else"}) + "\n")
    with pytest.raises(ValueError, match="not an aegis-finance-export"):
        portability.read_header(stranger)


@pytest.mark.asyncio
async def test_a_restore_without_replace_refuses_to_merge(
    async_db_session: AsyncSession, tmp_path: Path
) -> None:
    """Two ledgers merged by luck is worse than a refusal."""
    await _ledger(async_db_session)
    archive = tmp_path / "finance.jsonl.gz"
    await portability.export_finance(async_db_session, archive)
    with pytest.raises(Exception):
        await portability.restore_finance(async_db_session, archive)


@pytest.mark.asyncio
async def test_an_older_source_exports_what_it_has(tmp_path: Path) -> None:
    """The instance being read is usually a version behind the one
    reading it — that is what moving a ledger between instances means.
    A column it never had, or a table, is not an error."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    old = tmp_path / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{old}")
    async with engine.begin() as connection:
        # One table, a handful of its columns, and nothing else.
        await connection.execute(
            text(
                "CREATE TABLE finance_account (id INTEGER PRIMARY KEY, "
                "name TEXT, account_type TEXT, classification TEXT)"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO finance_account (id, name, account_type, "
                "classification) VALUES (1, 'AMEX', 'credit_card', 'liability')"
            )
        )
    archive = tmp_path / "old.jsonl.gz"
    async with AsyncSession(engine) as session:
        counts = await portability.export_finance(session, archive)
    await engine.dispose()

    assert counts == {"finance_account": 1}
    rows = [
        json.loads(line)
        for line in gzip.open(archive, "rt", encoding="utf-8").read().splitlines()[1:]
    ]
    assert rows[0]["row"] == {
        "id": 1,
        "name": "AMEX",
        "account_type": "credit_card",
        "classification": "liability",
    }
