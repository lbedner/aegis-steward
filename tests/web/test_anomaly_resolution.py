"""FW-09 on the Attention tab: say what an anomaly was, yourself.

The assistant proposes a resolution through the queue; a person reading
the Attention list can record one directly, through the same service.
A settled one leaves the open list and shows under Recently resolved,
with its words; one under review stays, saying why.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.web.dom import none, one, text, triggers


async def _anomaly(db: AsyncSession, key: str) -> int:
    from app.services.finance.domains.detection.insights.rules import (
        create_insight_if_new,
    )

    insight = await create_insight_if_new(
        db,
        owner_user_id=0,
        insight_type="large_transaction",
        dedup_key=key,
        severity="warning",
        title=f"Large charge {key}",
        body="Larger than usual.",
    )
    await db.commit()
    return int(insight.id)


class TestResolvingFromAttention:
    @pytest.mark.asyncio
    async def test_each_open_anomaly_offers_resolve(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        insight_id = await _anomaly(async_db_session, "res-web-1")
        page = client.get("/review/attention").text
        opener = one(page, f"#insight-{insight_id} [data-resolve]")
        assert opener.get("hx-get") == f"/review/insights/{insight_id}/resolve"

        dialog = client.get(f"/review/insights/{insight_id}/resolve").text
        assert (
            one(dialog, "form").get("hx-post")
            == f"/review/insights/{insight_id}/resolve"
        )
        one(dialog, 'select[name="state"]')
        one(dialog, 'textarea[name="note"]')

    @pytest.mark.asyncio
    async def test_settling_it_moves_it_to_recently_resolved(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        insight_id = await _anomaly(async_db_session, "res-web-2")
        answer = client.post(
            f"/review/insights/{insight_id}/resolve",
            data={"state": "legitimate", "note": "The dentist, planned."},
            headers={"HX-Current-URL": "http://t/review/attention"},
        )
        assert answer.status_code == 200
        assert "dialog:close" in triggers(answer)

        page = client.get("/review/attention").text
        none(page, f"#insight-{insight_id}")
        settled = one(page, f"#resolved-{insight_id}")
        assert "Confirmed legitimate" in text(settled)
        assert "The dentist, planned." in text(settled)

    @pytest.mark.asyncio
    async def test_under_review_stays_open_and_says_why(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        insight_id = await _anomaly(async_db_session, "res-web-3")
        client.post(
            f"/review/insights/{insight_id}/resolve",
            data={"state": "under_review", "note": "Called the bank."},
        )
        card = one(client.get("/review/attention").text, f"#insight-{insight_id}")
        assert "Called the bank." in text(one(card, "[data-review-note]"))

    @pytest.mark.asyncio
    async def test_an_unknown_state_comes_back_with_the_error(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        insight_id = await _anomaly(async_db_session, "res-web-4")
        answer = client.post(
            f"/review/insights/{insight_id}/resolve",
            data={"state": "whatever", "note": ""},
        )
        assert answer.status_code == 422
        one(answer.text, "[role=alert]")
