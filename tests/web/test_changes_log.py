"""Every edit is logged, and the last one can be undone (#325).

The user's own edits left no trace and nothing could be undone - the
condition people put on letting an assistant write at all. Now every
edit to a field people set is logged with who made it (you, an
assistant, an import, a sync) and its value before and after; a
transaction shows its history; and the newest action - a batch counts
as one - can be taken back.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import changelog
from app.services.finance.models import FinanceTransaction, FinanceTransactionChangelog
from app.services.finance.service import FinanceService
from tests.web.conftest import Ledger
from tests.web.dom import none, one, text
from tests.web.dom import select as select_all

CHANGES = "/settings/changes"


async def _mystery(finance: FinanceService) -> tuple[int, int]:
    """The ledger's uncategorized charge, and the Groceries category."""
    rows, _total = await finance.list_transactions(owner_user_id=None, page_size=50)
    mystery = next(t for t in rows if t.name == "Mystery charge")
    groceries = next(t for t in rows if t.name == "Market").category_id
    assert mystery.id is not None and groceries is not None
    return mystery.id, groceries


async def _category(db: AsyncSession, transaction_id: int) -> int | None:
    db.expire_all()
    row = (
        await db.exec(
            select(FinanceTransaction.category_id).where(
                FinanceTransaction.id == transaction_id
            )
        )
    ).one()
    return row


class TestYourEditIsLogged:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=6)  # edit, history, the log, undo, redo
    async def test_an_edit_shows_in_history_and_undoes(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        mystery, groceries = await _mystery(finance)

        hx.post(f"/transactions/{mystery}/categorize", data={"category_id": groceries})

        said = [
            text(li)
            for li in select_all(
                hx.get(f"/transactions/{mystery}/history").text, "[data-history] li"
            )
        ]
        # Filing it marks it reviewed too: both are the one edit's.
        assert any("Category: nothing → Food:Groceries" in s for s in said)
        assert any("Reviewed: no → yes" in s for s in said)
        assert all("You" in s for s in said)

        page = client.get(CHANGES).text
        entry = one(page, "#changes li[data-batch]")
        # One action, one sentence; reviewed rides along unsaid.
        assert text(one(entry, "[data-said]")) == (
            "Filed Mystery charge under Food:Groceries"
        )
        undo = one(entry, "[data-undo]")
        assert undo.get("hx-post") == f"{CHANGES}/undo"

        after = hx.post(f"{CHANGES}/undo").text

        assert await _category(async_db_session, mystery) is None
        # Undoing marks the action; it is not a line of its own.
        entry = one(after, "#changes li[data-batch]")
        one(entry, "[data-undone]")
        none(after, "[data-undo]")
        assert one(entry, "[data-redo]").get("hx-post") == f"{CHANGES}/redo"

        again = hx.post(f"{CHANGES}/redo").text

        assert await _category(async_db_session, mystery) == groceries
        entry = one(again, "#changes li[data-batch]")
        none(entry, "[data-undone]")
        one(entry, "[data-undo]")

    @pytest.mark.asyncio
    async def test_a_tag_added_is_logged_and_taken_back(
        self,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        mystery, _groceries = await _mystery(finance)

        with changelog.acting("you"):
            await finance.tag_transactions([mystery], "Work", owner_user_id=None)
            await async_db_session.flush()
        put_back, left = await changelog.undo(async_db_session, None)
        await async_db_session.flush()

        assert (put_back, left) == (1, 0)
        assert not (await finance.transaction_tags([mystery])).get(mystery)


class TestWhoAndWhat:
    @pytest.mark.asyncio
    async def test_nothing_is_logged_outside_an_action(
        self, finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        """Detection's bookkeeping, a migration: not edits."""
        mystery, groceries = await _mystery(finance)
        row = await async_db_session.get(FinanceTransaction, mystery)
        assert row is not None
        row.category_id = groceries
        await async_db_session.flush()

        assert (
            await async_db_session.exec(select(FinanceTransactionChangelog))
        ).all() == []

    @pytest.mark.asyncio
    async def test_undo_takes_back_yours_not_a_sync_after_it(
        self, finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        mystery, groceries = await _mystery(finance)
        row = await async_db_session.get(FinanceTransaction, mystery)
        assert row is not None
        with changelog.acting("you"):
            row.category_id = groceries
            await async_db_session.flush()
        with changelog.acting(changelog.SYNC):
            row.memo = "from the bank"
            await async_db_session.flush()

        put_back, _left = await changelog.undo(async_db_session, None)

        assert put_back == 1
        assert (row.category_id, row.memo) == (None, "from the bank")

    @pytest.mark.asyncio
    async def test_a_field_changed_again_since_is_left(
        self, finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        mystery, groceries = await _mystery(finance)
        row = await async_db_session.get(FinanceTransaction, mystery)
        assert row is not None
        with changelog.acting("you"):
            row.category_id = groceries
            await async_db_session.flush()
        row.category_id = None  # a change nobody logged
        await async_db_session.flush()

        assert await changelog.undo(async_db_session, None) == (0, 1)

    @pytest.mark.asyncio
    async def test_an_approved_card_is_the_assistants(
        self, finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
    ) -> None:
        from app.services.finance.domains.writes import approve, propose

        mystery, groceries = await _mystery(finance)
        card = await propose(
            async_db_session,
            "transaction.categorize",
            {"transaction_id": mystery, "category_id": groceries},
            owner_user_id=None,
            proposed_by_agent="illiana",
        )
        assert card.id is not None
        await approve(async_db_session, card.id, owner_user_id=None)

        logged = (
            await async_db_session.exec(select(FinanceTransactionChangelog))
        ).all()
        assert sorted((r.field, r.change_source) for r in logged) == [
            ("category_id", "illiana"),
            ("is_reviewed", "illiana"),
        ]


def test_the_settings_tab_names_it(client: TestClient) -> None:
    tabs = [
        a.get("href") for a in select_all(client.get(CHANGES).text, "#settings-nav a")
    ]
    assert CHANGES in tabs


@pytest.mark.asyncio
@pytest.mark.queryspy(threshold=3)  # asked before the new edit and after
async def test_a_new_edit_ends_the_chance_to_redo(
    finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
) -> None:
    """A redo over a newer edit would reapply a stale change."""
    mystery, groceries = await _mystery(finance)
    row = await async_db_session.get(FinanceTransaction, mystery)
    assert row is not None
    with changelog.acting("you"):
        row.category_id = groceries
        await async_db_session.flush()
    await changelog.undo(async_db_session, None)
    assert await changelog.last_redoable(async_db_session, None) is not None

    with changelog.acting("you"):
        row.memo = "a newer edit"
        await async_db_session.flush()

    assert await changelog.last_redoable(async_db_session, None) is None


def test_a_tag_reads_with_its_name_coming_or_going() -> None:
    from app.components.web_frontend.routes.finance.changes_log import _sentence

    names = {"tag": {5: "Work"}}
    payees = {1: "Dunkin'"}

    def row(old: str | None, new: str | None) -> FinanceTransactionChangelog:
        return FinanceTransactionChangelog(
            transaction_id=1,
            owner_user_id=0,
            field="tag",
            old_value=old,
            new_value=new,
            change_source="you",
        )

    assert _sentence([row(None, "5")], names, payees) == "Tagged Dunkin' “Work”"
    assert _sentence([row("5", None)], names, payees) == "Took “Work” off Dunkin'"


@pytest.mark.asyncio
async def test_a_charge_is_called_what_the_register_calls_it(
    client: TestClient,
    hx: TestClient,
    finance: FinanceService,
    async_db_session: AsyncSession,
    ledger: Ledger,
) -> None:
    """The payee someone named, not the bank's descriptor."""
    mystery, groceries = await _mystery(finance)
    payee = await finance.create_merchant("Shop Rite", owner_user_id=None)
    assert payee.id is not None
    await finance.assign_merchant([mystery], payee.id, owner_user_id=None)
    await async_db_session.commit()

    hx.post(f"/transactions/{mystery}/categorize", data={"category_id": groceries})

    said = text(one(client.get(CHANGES).text, "#changes li[data-batch] [data-said]"))
    assert said == "Filed Shop Rite under Food:Groceries"
