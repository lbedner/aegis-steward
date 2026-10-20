"""A transfer shows its other half (#317).

Detection paired legs and took them out of spending, and nothing on the
register said so: a row just wasn't counted. A near miss was dropped
silently and counted as spending. Now a paired row wears a mark that
opens both halves and can break the pair; a near miss is offered in
Attention, to confirm or turn down.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection import detect_transfers
from app.services.finance.models import (
    FinanceInsight,
    FinanceTransaction,
    FinanceTransactionSplit,
    FinanceTransfer,
)
from app.services.finance.schemas import SplitPart
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date
from tests.web.conftest import REGISTER, Ledger
from tests.web.dom import none, one, text

ATTENTION = "/review/attention"


async def _legs(
    finance: FinanceService,
    db: AsyncSession,
    ledger: Ledger,
    *,
    days_apart: int,
    name: str,
) -> tuple[int, int]:
    """$500 out of checking and into savings, then a detection pass."""
    today = current_date()
    out = await finance.create_transaction(
        account_id=ledger.checking, amount=-50_000, txn_date=today, name=name
    )
    into = await finance.create_transaction(
        account_id=ledger.savings,
        amount=50_000,
        txn_date=today - timedelta(days=days_apart),
        name=name,
    )
    await detect_transfers(db, owner_user_id=None, today=today)
    await db.commit()
    assert out.id is not None and into.id is not None
    return out.id, into.id


async def _flags(db: AsyncSession, *ids: int) -> set[tuple[bool, bool]]:
    """Each leg's (is_transfer, excluded_from_reports), read fresh."""
    db.expire_all()
    rows = (
        await db.exec(select(FinanceTransaction).where(FinanceTransaction.id.in_(ids)))
    ).all()
    assert len(rows) == len(ids)
    return {(r.is_transfer, r.excluded_from_reports) for r in rows}


class TestAPairShowsBothHalves:
    @pytest.mark.asyncio
    async def test_the_row_is_marked_and_opens_its_other_half(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        out, into = await _legs(
            finance, async_db_session, ledger, days_apart=0, name="ONLINE TRANSFER"
        )

        mark = one(client.get(REGISTER).text, f"#txn-{out} [data-transfer]")
        pair = hx.get(mark.get("hx-get")).text

        assert mark.get("hx-get") == f"/transactions/{out}/transfer"
        assert "Savings" in text(one(pair, f"[data-leg='{into}']"))
        assert one(pair, "[data-break]").get("hx-post") == (
            f"/transactions/{out}/transfer/break"
        )

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # detection before the break and after
    async def test_broken_both_count_again_and_it_does_not_come_back(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        out, into = await _legs(
            finance, async_db_session, ledger, days_apart=0, name="ONLINE TRANSFER"
        )

        hx.post(f"/transactions/{into}/transfer/break")
        await detect_transfers(
            async_db_session, owner_user_id=None, today=current_date()
        )

        assert await _flags(async_db_session, out, into) == {(False, False)}
        transfer = (await async_db_session.exec(select(FinanceTransfer))).one()
        assert transfer.status == "rejected"


class TestANearMissIsOffered:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=4)  # the legs read before the answer and after
    async def test_it_is_an_attention_item_and_confirming_pairs_it(
        self,
        client: TestClient,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        """Same amount two days apart, nothing in the names: not enough to
        hide on its own, too close to leave unsaid."""
        out, into = await _legs(
            finance, async_db_session, ledger, days_apart=2, name="Deposit"
        )
        assert await _flags(async_db_session, out) == {(False, False)}
        (offer,) = (
            await async_db_session.exec(
                select(FinanceInsight).where(
                    FinanceInsight.insight_type == "transfer_suggested"
                )
            )
        ).all()

        offer_id = offer.id
        item = one(client.get(ATTENTION).text, f"#insight-{offer_id}")
        confirm = one(item, "[data-confirm]").get("hx-post")
        assert confirm == f"/transactions/{out}/transfer/confirm?insight={offer_id}"
        assert one(item, "[data-decline]").get("hx-post") == (
            f"/transactions/{out}/transfer/decline?insight={offer_id}"
        )
        none(item, "[data-resolve]")
        hx.post(confirm)

        assert await _flags(async_db_session, out, into) == {(True, True)}
        none(client.get(ATTENTION).text, f"#insight-{offer_id}")

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # detection before the answer and after
    async def test_turned_down_it_stays_spending_and_is_not_offered_again(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        out, _into = await _legs(
            finance, async_db_session, ledger, days_apart=2, name="Deposit"
        )
        offer = (await async_db_session.exec(select(FinanceInsight))).one()

        hx.post(f"/transactions/{out}/transfer/decline?insight={offer.id}")
        await detect_transfers(
            async_db_session, owner_user_id=None, today=current_date()
        )

        assert await _flags(async_db_session, out) == {(False, False)}
        transfer = (await async_db_session.exec(select(FinanceTransfer))).one()
        assert transfer.status == "rejected"


@pytest.mark.asyncio
async def test_removing_one_half_counts_the_other_again(
    finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
) -> None:
    """It kept ``excluded_from_reports``: unpaired, and still uncounted."""
    out, into = await _legs(
        finance, async_db_session, ledger, days_apart=0, name="ONLINE TRANSFER"
    )

    await finance.soft_delete_transactions([out], owner_user_id=None)
    await async_db_session.commit()

    assert await _flags(async_db_session, into) == {(False, False)}
    # The pair is kept, so an Undo of the delete has it back.
    (transfer,) = (await async_db_session.exec(select(FinanceTransfer))).all()
    assert transfer.status == "confirmed"


@pytest.mark.asyncio
@pytest.mark.queryspy(threshold=3)  # detection twice
async def test_a_sure_pair_takes_a_leg_an_offer_held(
    finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
) -> None:
    """Offered first, then the real other half arrives: the offer goes
    and its Attention item with it."""
    out, _into = await _legs(
        finance, async_db_session, ledger, days_apart=2, name="Deposit"
    )
    real = await finance.create_transaction(
        account_id=ledger.card,
        amount=50_000,
        txn_date=current_date(),
        name="ONLINE TRANSFER",
    )
    await detect_transfers(async_db_session, owner_user_id=None, today=current_date())
    await async_db_session.commit()

    (transfer,) = (await async_db_session.exec(select(FinanceTransfer))).all()
    assert (transfer.from_transaction_id, transfer.to_transaction_id) == (out, real.id)
    assert transfer.status == "confirmed"
    (offer,) = (await async_db_session.exec(select(FinanceInsight))).all()
    assert offer.status == "dismissed"


@pytest.mark.asyncio
async def test_an_offer_whose_other_half_was_removed_confirms_nothing(
    hx: TestClient,
    finance: FinanceService,
    async_db_session: AsyncSession,
    ledger: Ledger,
) -> None:
    out, into = await _legs(
        finance, async_db_session, ledger, days_apart=2, name="Deposit"
    )
    await finance.soft_delete_transactions([into], owner_user_id=None)
    await async_db_session.commit()

    answer = hx.post(f"/transactions/{out}/transfer/confirm")

    assert answer.status_code == 404
    assert await _flags(async_db_session, out) == {(False, False)}


@pytest.mark.asyncio
async def test_an_offer_whose_other_half_was_removed_leaves_attention(
    client: TestClient,
    finance: FinanceService,
    async_db_session: AsyncSession,
    ledger: Ledger,
) -> None:
    """Both its buttons answer nothing now, and it has no Dismiss: left,
    it would sit in Attention and hold its other leg for good."""
    out, into = await _legs(
        finance, async_db_session, ledger, days_apart=2, name="Deposit"
    )
    (offer,) = (await async_db_session.exec(select(FinanceInsight))).all()
    offer_id = offer.id

    await finance.soft_delete_transactions([into], owner_user_id=None)
    await async_db_session.commit()

    none(client.get(ATTENTION).text, f"#insight-{offer_id}")
    assert (await async_db_session.exec(select(FinanceTransfer))).all() == []


class TestAnOfferAndAPairAreAnsweredApart:
    @pytest.mark.asyncio
    async def test_a_stale_offer_button_cannot_break_a_pair(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        """Attention open, a sync made the pair sure: turning the old
        offer down must not undo a pair the person never saw."""
        out, into = await _legs(
            finance, async_db_session, ledger, days_apart=0, name="ONLINE TRANSFER"
        )

        answer = hx.post(f"/transactions/{out}/transfer/decline")

        assert answer.status_code == 404
        assert await _flags(async_db_session, out, into) == {(True, True)}

    @pytest.mark.asyncio
    async def test_breaking_is_for_a_pair_not_an_offer(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        out, _into = await _legs(
            finance, async_db_session, ledger, days_apart=2, name="Deposit"
        )

        answer = hx.post(f"/transactions/{out}/transfer/break")

        assert answer.status_code == 404
        transfer = (await async_db_session.exec(select(FinanceTransfer))).one()
        assert transfer.status == "suggested"

    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # the split's category reads, twice
    async def test_turning_an_offer_down_leaves_its_rows_as_they_were(
        self,
        hx: TestClient,
        finance: FinanceService,
        async_db_session: AsyncSession,
        ledger: Ledger,
    ) -> None:
        """An offer never flagged its rows, so turning it down must not
        clear what a person set: a split, a row left out of reports."""
        today = current_date()
        out = await finance.create_transaction(
            account_id=ledger.checking, amount=-50_000, txn_date=today, name="COSTCO"
        )
        assert out.id is not None
        food = await finance.get_or_create_category_from_hint("Food:Groceries")
        home = await finance.get_or_create_category_from_hint("Home:Supplies")
        await finance.split_transaction(
            out.id,
            [
                SplitPart(amount=30_000, category_id=food.id),
                SplitPart(amount=20_000, category_id=home.id),
            ],
            owner_user_id=None,
        )
        into = await finance.create_transaction(
            account_id=ledger.savings,
            amount=50_000,
            txn_date=today - timedelta(days=2),
            name="Deposit",
        )
        assert into.id is not None
        into.excluded_from_reports = True
        async_db_session.add(into)
        out_id, into_id = out.id, into.id
        await detect_transfers(async_db_session, owner_user_id=None, today=today)
        await async_db_session.commit()

        answer = hx.post(f"/transactions/{out_id}/transfer/decline")

        assert answer.status_code == 200
        assert await _flags(async_db_session, into_id) == {(False, True)}
        lines = (
            await async_db_session.exec(
                select(FinanceTransactionSplit).where(
                    FinanceTransactionSplit.parent_transaction_id == out_id
                )
            )
        ).all()
        assert len(lines) == 2
