"""A statement on file checks the register (#321).

A filed statement's balance was read and nothing compared it with the
register, so a missing transaction stayed missing until somebody went
looking. Now reading one compares its balance with the register on its
date: a gap is an Attention item that opens Correct the balance already
filled in, and a match reconciles the account through that date, which
its page says.
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.models import Document, DocumentPage
from app.services.finance.models import FinanceAccount, FinanceInsight
from app.services.finance.utils import current_date
from tests.web.conftest import Ledger
from tests.web.dom import none, one, text

ATTENTION = "/review/attention"


async def _statement(db: AsyncSession, ledger: Ledger, balance: str, on: date) -> None:
    """Checking's statement, ending 4321, filed and read."""
    from app.services.documents.domains.reading.statements import (
        check_filed_statement,
    )

    checking = await db.get(FinanceAccount, ledger.checking)
    assert checking is not None
    checking.mask = "4321"
    document = Document(
        title="checking.pdf",
        filename="checking.pdf",
        storage_key="testcase/checking-statement.pdf",
        media_type="application/pdf",
        content_hash=f"testcase-checking-{balance}",
        size_bytes=9,
    )
    db.add_all([checking, document])
    await db.flush()
    db.add(
        DocumentPage(
            document_id=document.id,
            page_number=1,
            status="read",
            method="text",
            text=f"Account ending 4321\nEnding balance as of {on:%m/%d/%y}: {balance}",
        )
    )
    await db.flush()
    assert document.id is not None
    await check_filed_statement(db, document.id)
    await db.commit()


class TestAGap:
    @pytest.mark.asyncio
    async def test_it_needs_attention_and_opens_the_correction_filled_in(
        self,
        client: TestClient,
        hx: TestClient,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        """The register says $455.00 today (the ledger's $500 payroll less
        $45 of groceries); the bank says $480.00."""
        today = current_date()
        await _statement(async_db_session, ledger, "$480.00", today)
        (gap,) = (
            await async_db_session.exec(
                select(FinanceInsight).where(
                    FinanceInsight.insight_type == "statement_gap"
                )
            )
        ).all()
        gap_id = gap.id

        item = one(client.get(ATTENTION).text, f"#insight-{gap_id}")
        assert "$25.00" in text(item)
        opens = one(item, "[data-correct]").get("hx-get")
        form = hx.get(opens).text

        assert one(form, "input[name=statement_date]").get("value") == today.isoformat()
        assert one(form, "input[name=statement_balance]").get("value") == "480.00"

        hx.post(
            f"/accounts/{ledger.checking}/reconcile",
            data={"statement_date": today.isoformat(), "statement_balance": "480.00"},
        )
        none(client.get(ATTENTION).text, f"#insight-{gap_id}")


class TestAMatch:
    @pytest.mark.asyncio
    async def test_it_reconciles_the_account_through_its_date(
        self, client: TestClient, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        today = current_date()
        await _statement(async_db_session, ledger, "$455.00", today)

        assert (await async_db_session.exec(select(FinanceInsight))).all() == []
        header = one(
            client.get(f"/accounts/{ledger.checking}").text, "#account-detail > header"
        )
        assert "Reconciled through" in text(one(header, "[data-reconciled]"))


@pytest.mark.asyncio
async def test_a_cards_statement_prints_what_is_owed_or_owed_back(
    async_db_session: AsyncSession, ledger: Ledger
) -> None:
    """The ledger's Visa owes $27.00 by its register (-$27.00): a
    statement saying $27.00 owed agrees. One printing a $27.00 credit
    (-$27.00) is off by $54.00, where a sign rule of -abs read $0."""
    from app.services.finance.domains.detection.insights.statement_gaps import (
        check_statement,
    )

    card = await async_db_session.get(FinanceAccount, ledger.card)
    assert card is not None
    day = current_date()

    owed = await check_statement(
        async_db_session, card, statement_date=day, printed=2_700
    )
    credit = await check_statement(
        async_db_session, card, statement_date=day, printed=-2_700
    )

    assert (owed, credit) == (0, 5_400)


@pytest.mark.asyncio
async def test_a_correction_the_missing_row_made_stale_is_a_gap(
    async_db_session: AsyncSession, ledger: Ledger
) -> None:
    """Corrected to $480.00 with a $25.00 adjustment; then the missing
    $25.00 deposit arrives, so the register says $505.00. The check
    measured as if the adjustment were not there, read a match, and left
    the register $25.00 over its statement."""
    from app.services.finance.domains.detection.insights.statement_gaps import (
        check_statement,
    )
    from app.services.finance.domains.ledger.accounts import reconcile_account
    from app.services.finance.domains.ledger.transactions import create_transaction

    day = current_date()
    await reconcile_account(
        async_db_session,
        ledger.checking,
        statement_date=day,
        statement_balance=48_000,
    )
    await create_transaction(
        async_db_session,
        account_id=ledger.checking,
        amount=2_500,
        txn_date=day,
        name="Deposit",
    )
    checking = await async_db_session.get(FinanceAccount, ledger.checking)
    assert checking is not None

    gap = await check_statement(
        async_db_session, checking, statement_date=day, printed=48_000
    )

    assert gap == -2_500
