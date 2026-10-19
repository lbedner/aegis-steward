"""A reviewed row looks reviewed (#322); a pending row says pending, and
its edits survive posting (#323).

``is_reviewed`` was set by categorizing and never read: now it shows,
filters, and is set directly. A pre-auth was unmarked, and what you set
on it was lost when its posted row replaced it.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.web.conftest import REGISTER, Ledger
from tests.web.dom import none, one, select, table_rows, text


def names(markup: str) -> list[str]:
    return [
        text(row["Payee"]).split(" ")[0]
        for row in table_rows(markup, "#register table")
    ]


async def _row_id(finance: FinanceService, name: str) -> int:
    rows, _total = await finance.list_transactions(owner_user_id=None, page_size=50)
    found = next(t for t in rows if t.name == name)
    assert found.id is not None
    return found.id


class TestReviewed:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=5)  # mark, the filter, the mark, the page
    async def test_marked_reviewed_it_shows_and_filters(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        ledger: Ledger,
    ) -> None:
        gas = await _row_id(finance, "Gas")
        mystery = await _row_id(finance, "Mystery charge")

        answer = hx.post(
            "/transactions/reviewed", data={"transaction_ids": [gas, mystery]}
        ).text

        one(answer, f"#txn-{gas}[hx-swap-oob] [data-reviewed]")
        assert sorted(names(client.get(f"{REGISTER}?reviewed=yes").text)) == [
            "Gas",
            "Mystery",
        ]
        hx.post(
            "/transactions/reviewed",
            data={"transaction_ids": [gas], "reviewed": "false"},
        )
        page = client.get(f"{REGISTER}?reviewed=no").text
        assert "Gas" in names(page) and "Mystery" not in names(page)

    @pytest.mark.asyncio
    async def test_each_row_offers_it_and_the_bar_does_too(
        self, client: TestClient, finance: FinanceService, ledger: Ledger
    ) -> None:
        gas = await _row_id(finance, "Gas")
        page = client.get(REGISTER).text

        item = next(
            li
            for li in select(page, f"#txn-{gas} [role=menu] li")
            if "reviewed" in text(li)
        )
        assert text(item) == "Mark reviewed"
        assert one(item, "[hx-post]").get("hx-post") == "/transactions/reviewed"
        assert one(page, "#bulk-actions [hx-post='/transactions/reviewed']") is not None


class TestPending:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the register before and after
    async def test_a_pending_row_is_marked_and_the_rule_is_stated(
        self,
        client: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        none(client.get(REGISTER).text, "[data-pending-note]")
        pending = await finance.create_transaction(
            account_id=ledger.card,
            amount=-1_250,
            txn_date=current_date(),
            name="Diner",
            pending=True,
        )
        await async_db_session.commit()

        page = client.get(REGISTER).text

        one(page, f"#txn-{pending.id} [data-pending]")
        assert "count in spending" in text(one(page, "[data-pending-note]"))

    @pytest.mark.asyncio
    async def test_what_you_set_on_a_pre_auth_survives_its_posting(
        self, finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        from app.services.finance.adapters.providers.connections.posting import (
            carry_edits,
        )

        day = current_date() - timedelta(days=1)
        pending = await finance.create_transaction(
            account_id=ledger.card,
            amount=-1_250,
            txn_date=day,
            name="Diner",
            pending=True,
        )
        posted = await finance.create_transaction(
            account_id=ledger.card, amount=-1_400, txn_date=day, name="DINER #12"
        )
        groceries = await finance.get_or_create_category_from_hint("Food:Groceries")
        pending.category_id, pending.category_source = groceries.id, "user"
        pending.memo, pending.is_reviewed = "lunch with Sam", True
        async_db_session.add(pending)
        await async_db_session.flush()
        assert pending.id is not None and posted.id is not None
        await finance.tag_transactions([pending.id], "Work", owner_user_id=None)
        await async_db_session.flush()

        await carry_edits(async_db_session, pending, posted)
        await async_db_session.flush()

        assert (posted.category_id, posted.memo, posted.is_reviewed) == (
            groceries.id,
            "lunch with Sam",
            True,
        )
        tags = await finance.transaction_tags([posted.id])
        assert [t.name for t in tags.get(posted.id, [])] == ["Work"]

    @pytest.mark.asyncio
    async def test_a_tag_the_posted_row_already_has_does_not_break_the_sync(
        self, finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        from app.services.finance.adapters.providers.connections.posting import (
            carry_edits,
        )

        day = current_date() - timedelta(days=1)
        pending = await finance.create_transaction(
            account_id=ledger.card,
            amount=-1_250,
            txn_date=day,
            name="Diner",
            pending=True,
        )
        posted = await finance.create_transaction(
            account_id=ledger.card, amount=-1_250, txn_date=day, name="DINER #12"
        )
        assert pending.id is not None and posted.id is not None
        await finance.tag_transactions(
            [pending.id, posted.id], "Work", owner_user_id=None
        )
        await async_db_session.flush()

        await carry_edits(async_db_session, pending, posted)
        await async_db_session.flush()

        tags = await finance.transaction_tags([posted.id])
        assert [t.name for t in tags.get(posted.id, [])] == ["Work"]
