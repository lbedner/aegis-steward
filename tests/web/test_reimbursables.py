"""A charge that will be paid back links to the money that pays it (#327).

An HSA bill, a work expense, friends' shares: marked on the charge with
what is expected and from whom, linked to the repayment, and once repaid
in full the pair leaves spending together - and comes back exactly as it
was when the repayment is unlinked.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select as sql_select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import FinanceTransaction, FinanceTransactionChangelog
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.web.conftest import Ledger
from tests.web.dom import none, one, select, table_rows, text, triggers

OPEN = "/review/reimbursable"


async def _rows(
    finance: FinanceService, db: AsyncSession, ledger: Ledger, back: int
) -> tuple[int, int]:
    """The ledger's $30.00 Market charge, and ``back`` cents coming in."""
    rows, _total = await finance.list_transactions(owner_user_id=None, page_size=50)
    charge = next(t for t in rows if t.amount == -3_000)
    repayment = await finance.create_transaction(
        account_id=ledger.checking,
        amount=back,
        txn_date=current_date() + timedelta(days=1),
        name="VENMO FROM SAM",
    )
    await db.commit()
    assert charge.id is not None and repayment.id is not None
    return charge.id, repayment.id


async def _flags(db: AsyncSession, *ids: int) -> list[bool]:
    """Each row's report flag, in the order asked, in one read."""
    db.expire_all()
    rows = (
        await db.exec(
            sql_select(FinanceTransaction).where(FinanceTransaction.id.in_(ids))
        )
    ).all()
    flags = {t.id: t.excluded_from_reports for t in rows}
    return [flags[i] for i in ids]


class TestMarkingAndRepaying:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=8)  # six requests, each a read of the charge
    async def test_repaid_in_full_the_pair_leaves_spending_until_unlinked(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        charge, back = await _rows(finance, async_db_session, ledger, 3_000)
        dialog = f"/transactions/{charge}/reimbursable"

        form = one(hx.get(dialog).text, "form[data-mark]")
        assert one(form, 'input[name="expected"]').get("value") == "30.00"
        marked = hx.post(dialog, data={"expected": "30", "from_name": "Sam"}).text
        one(marked, "[data-reimbursable]")
        assert [
            text(r["Charge"])
            for r in table_rows(client.get(OPEN).text, "#reimbursable table")
        ] == ["Market"]

        option = next(
            o for o in select(marked, "form[data-link] option") if "VENMO" in text(o)
        )
        settled = hx.post(
            f"{dialog}/repayments", data={"repayment_id": option.get("value")}
        ).text

        assert "out of spending" in text(one(settled, "[data-settled]"))
        assert await _flags(async_db_session, charge, back) == [True, True]
        # The link is the action; the flags it moved are not edits an Undo
        # could take back without it.
        logged = (
            await async_db_session.exec(sql_select(FinanceTransactionChangelog))
        ).all()
        assert [r for r in logged if r.field == "excluded_from_reports"] == []
        none(client.get(OPEN).text, "#reimbursable table tbody tr")

        hx.delete(f"{dialog}/repayments/{back}")
        assert await _flags(async_db_session, charge, back) == [False, False]

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # mark, then link
    async def test_a_share_settles_but_stays_in_spending(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        """A friend's $10 of a $30 dinner: leaving part of one charge out
        would need a split, so it is settled and still counted."""
        charge, back = await _rows(finance, async_db_session, ledger, 1_000)
        dialog = f"/transactions/{charge}/reimbursable"
        hx.post(dialog, data={"expected": "10"})

        settled = hx.post(f"{dialog}/repayments", data={"repayment_id": str(back)}).text

        assert text(one(settled, "[data-settled]")) == "Settled."
        assert await _flags(async_db_session, charge, back) == [False, False]

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # mark, let go, look again
    async def test_letting_it_go_closes_the_dialog(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        charge, _back = await _rows(finance, async_db_session, ledger, 3_000)
        dialog = f"/transactions/{charge}/reimbursable"
        hx.post(dialog, data={"expected": "30"})

        answer = hx.delete(dialog)

        assert "dialog:close" in triggers(answer)
        one(hx.get(dialog).text, "form[data-mark]")


class TestWhatCannotBePaidBack:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the refusal redraws the dialog
    async def test_money_that_came_in_is_refused(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        _charge, back = await _rows(finance, async_db_session, ledger, 3_000)

        answer = hx.post(f"/transactions/{back}/reimbursable", data={"expected": ""})

        assert answer.status_code == 422
        assert "Only money that went out" in answer.text

    def test_the_row_offers_it_only_for_money_out(
        self, client: TestClient, ledger: Ledger
    ) -> None:
        page = client.get("/accounts/all").text
        offered = [
            a.get("hx-get") for a in select(page, "#register [hx-get$='/reimbursable']")
        ]
        # Four of the five ledger rows are money out; payroll is not.
        assert len(offered) == 4


@pytest.mark.asyncio
async def test_a_charge_that_is_gone_is_owed_nothing(
    client: TestClient,
    hx: TestClient,
    finance: FinanceService,
    async_db_session: AsyncSession,
    ledger: Ledger,
) -> None:
    """A pre-auth marked to be paid back, retired when it never posted
    (#540): its mark stayed open and the money read as owed."""
    charge, _back = await _rows(finance, async_db_session, ledger, 3_000)
    hx.post(f"/transactions/{charge}/reimbursable", data={"expected": "30"})

    await finance.soft_delete_transactions([charge], owner_user_id=None)
    await async_db_session.commit()

    none(client.get(OPEN).text, "#reimbursable table")
