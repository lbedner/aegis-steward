"""A newly detected bill says so (#340).

New detections waited in Bills' Detected tab with a count beside it. A
forgotten subscription is caught the week it appears only if it is
announced: an Attention item, confirmed or muted where it is read.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.rules import generate_insights
from app.services.finance.models import FinanceInsight, FinanceRecurringStream
from tests.web.conftest import Streams
from tests.web.dom import none, one, text

ATTENTION = "/review/attention"


async def _announced(db: AsyncSession) -> list[FinanceInsight]:
    await generate_insights(db, owner_user_id=None)
    await db.commit()
    return list(
        (
            await db.exec(
                select(FinanceInsight).where(
                    FinanceInsight.insight_type == "new_recurring",
                    FinanceInsight.status == "new",
                )
            )
        ).all()
    )


class TestANewBillIsAnnounced:
    @pytest.mark.asyncio
    async def test_it_is_an_attention_item_with_confirm_and_mute(
        self, client: TestClient, streams: Streams, async_db_session: AsyncSession
    ) -> None:
        (news,) = await _announced(async_db_session)
        assert news.related_stream_id == streams.netflix

        item = one(client.get(ATTENTION).text, f"#insight-{news.id}")
        assert "New bill: Netflix" in text(item)
        for verb in ("confirm", "mute"):
            assert one(item, f"[data-{verb}]").get("hx-post") == (
                f"/bills/{streams.netflix}/{verb}?insight={news.id}"
            )
        none(item, "[data-resolve]")

    @pytest.mark.asyncio
    async def test_confirming_it_there_makes_it_a_bill_and_clears_it(
        self,
        client: TestClient,
        hx: TestClient,
        streams: Streams,
        async_db_session: AsyncSession,
    ) -> None:
        (news,) = await _announced(async_db_session)

        answer = hx.post(f"/bills/{streams.netflix}/confirm?insight={news.id}")

        assert answer.status_code == 200
        one(answer.text, "#review-nav[hx-swap-oob]")
        stream = await async_db_session.get(FinanceRecurringStream, streams.netflix)
        assert stream is not None
        await async_db_session.refresh(stream)
        assert stream.is_user_confirmed
        none(client.get(ATTENTION).text, f"#insight-{news.id}")

    @pytest.mark.asyncio
    async def test_a_bill_confirmed_in_bills_takes_its_news_back(
        self, streams: Streams, async_db_session: AsyncSession
    ) -> None:
        (news,) = await _announced(async_db_session)
        stream = await async_db_session.get(FinanceRecurringStream, streams.netflix)
        assert stream is not None
        stream.is_user_confirmed = True
        async_db_session.add(stream)
        await async_db_session.commit()

        await generate_insights(async_db_session, owner_user_id=None)
        await async_db_session.commit()

        assert await async_db_session.get(FinanceInsight, news.id) is None

    @pytest.mark.asyncio
    async def test_a_proposal_from_weeks_ago_is_not_news(
        self, streams: Streams, async_db_session: AsyncSession
    ) -> None:
        """The first pass after this shipped must not announce every
        proposal ever made."""
        stream = await async_db_session.get(FinanceRecurringStream, streams.netflix)
        assert stream is not None
        stream.created_at = stream.created_at - timedelta(days=30)
        async_db_session.add(stream)
        await async_db_session.commit()

        assert await _announced(async_db_session) == []
