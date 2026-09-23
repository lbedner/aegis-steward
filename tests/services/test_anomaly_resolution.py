"""FW-09: an anomaly can say what it turned out to be.

The analyst raises a large charge; the person explains it in chat; the
assistant proposes the reading and the person confirms it. Until now an
insight was new, seen, dismissed or actioned - enough for a badge, not
enough to record "that was the dentist, expected" or "a duplicate of
Tuesday's". A resolution says which, keeps the person's words, and takes
the insight off every reader that asks for what is still open.

Resolving never edits the transaction it is about. A mis-categorized
reading is a resolution plus a separate category proposal, not a silent
fix.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.detection.insights.rules import create_insight_if_new
from app.services.finance.domains.writes.queue import approve, propose, reject
from app.services.finance.models import FinanceInsight

OWNER = 1


async def _raised(db: AsyncSession, key: str = "large_txn:991") -> FinanceInsight:
    insight = await create_insight_if_new(
        db,
        owner_user_id=OWNER,
        insight_type="large_transaction",
        dedup_key=key,
        severity="warning",
        title="Large charge: $1,240.00 at Hudson Dental",
        body="Larger than anything at this payee before.",
    )
    assert insight is not None
    return insight


class TestTheAssistantProposesAReading:
    @pytest.mark.asyncio
    async def test_approving_records_the_state_and_the_words(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        insight = await _raised(db)
        card = await propose(
            db,
            "insight.resolve",
            {
                "insight_id": insight.id,
                "state": "legitimate",
                "note": "Crown for Marisa, planned since June.",
            },
            owner_user_id=OWNER,
            proposed_by_agent="steward",
        )
        assert (await db.get(FinanceInsight, insight.id)).status == "new"

        await approve(db, int(card.id), owner_user_id=OWNER)

        await db.refresh(insight)
        assert insight.resolution == "legitimate"
        assert insight.resolution_note == "Crown for Marisa, planned since June."
        assert insight.status == "actioned"
        assert insight.resolved_at is not None

    @pytest.mark.asyncio
    async def test_rejecting_leaves_it_open(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        insight = await _raised(db)
        card = await propose(
            db,
            "insight.resolve",
            {
                "insight_id": insight.id,
                "state": "duplicate",
                "note": "Same as Tuesday.",
            },
            owner_user_id=OWNER,
        )
        await reject(db, int(card.id), owner_user_id=OWNER)
        await db.refresh(insight)
        assert insight.status == "new" and insight.resolution is None

    @pytest.mark.asyncio
    async def test_an_unknown_state_dies_at_the_door(
        self, async_db_session: AsyncSession
    ) -> None:
        insight = await _raised(async_db_session)
        with pytest.raises(ValueError):
            await propose(
                async_db_session,
                "insight.resolve",
                {"insight_id": insight.id, "state": "whatever", "note": ""},
                owner_user_id=OWNER,
            )

    @pytest.mark.asyncio
    async def test_the_card_names_the_anomaly_and_the_reading(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes import describe_change

        db = async_db_session
        insight = await _raised(db)
        card = await propose(
            db,
            "insight.resolve",
            {
                "insight_id": insight.id,
                "state": "miscategorized",
                "note": "Not dining.",
            },
            owner_user_id=OWNER,
        )
        said = {row.label: row.value for row in await describe_change(db, card)}
        assert said["Anomaly"] == "Large charge: $1,240.00 at Hudson Dental"
        assert said["Reading"] == "Mis-categorized"
        assert said["Note"] == "Not dining."


class TestWhatAResolutionDoes:
    @pytest.mark.asyncio
    async def test_under_review_keeps_it_open_with_the_note(
        self, async_db_session: AsyncSession
    ) -> None:
        """Not settled - being looked into. Still in front of the reader,
        now saying why."""
        from app.services.finance.domains.planning.insights import resolve_insight

        db = async_db_session
        insight = await _raised(db)
        await resolve_insight(
            db,
            int(insight.id),
            state="under_review",
            note="Called the bank.",
            owner_user_id=OWNER,
        )
        await db.refresh(insight)
        assert insight.status == "new"
        assert insight.resolution == "under_review"
        assert insight.resolution_note == "Called the bank."

    @pytest.mark.asyncio
    async def test_the_next_detector_run_does_not_raise_it_again(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.planning.insights import resolve_insight

        db = async_db_session
        insight = await _raised(db)
        await resolve_insight(
            db, int(insight.id), state="legitimate", note="", owner_user_id=OWNER
        )
        assert (
            await create_insight_if_new(
                db,
                owner_user_id=OWNER,
                insight_type="large_transaction",
                dedup_key="large_txn:991",
                severity="warning",
                title="again",
                body="again",
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_it_leaves_what_the_analyst_reads(
        self, async_db_session: AsyncSession
    ) -> None:
        """The report flags what is still new; a settled question must not
        be re-flagged in tomorrow's narrative."""
        from app.services.finance.domains.planning.insights import resolve_insight
        from app.services.finance.service import FinanceService

        db = async_db_session
        insight = await _raised(db)
        await resolve_insight(
            db, int(insight.id), state="legitimate", note="", owner_user_id=OWNER
        )
        open_now = await FinanceService(db).list_insights(
            owner_user_id=OWNER, status="new"
        )
        assert insight.id not in {i.id for i in open_now}

    @pytest.mark.asyncio
    async def test_a_resolved_missed_bill_survives_its_retraction(
        self, async_db_session: AsyncSession
    ) -> None:
        """The missed-bill rule deletes its alert once the payment lands.
        A resolved one is a record now, not a claim - deleting it would
        erase what the person said about it."""
        from app.services.finance.domains.detection import generate_insights
        from app.services.finance.domains.planning.insights import resolve_insight
        from app.services.finance.service import FinanceService
        from tests.services._finance_factories import seed_account
        from tests.services.test_finance_insights import _insights_of, _stream

        db = async_db_session
        account = await seed_account(FinanceService(db))
        stream = await _stream(
            db,
            is_user_confirmed=True,
            account_id=account.id,
            last_date=date(2026, 6, 1),
            next_expected_date=date(2026, 7, 1),
        )
        await generate_insights(db, owner_user_id=OWNER, today=date(2026, 7, 10))
        [alert] = await _insights_of(db, "missed_recurring")
        await resolve_insight(
            db,
            int(alert.id),
            state="expected_missing",
            note="Paid by phone.",
            owner_user_id=OWNER,
        )

        stream.last_date = date(2026, 7, 12)
        stream.next_expected_date = date(2026, 8, 12)
        db.add(stream)
        await db.flush()
        await generate_insights(db, owner_user_id=OWNER, today=date(2026, 7, 13))

        [kept] = await _insights_of(db, "missed_recurring")
        assert kept.resolution_note == "Paid by phone."


class TestTheAssistantCanNameIt:
    """She reads open anomalies in her snapshot. To propose a resolution
    she has to name the insight, so each line carries its id - and one
    under review carries the note, so she does not ask the same question
    the person already answered."""

    def test_each_open_anomaly_carries_its_id_and_any_review_note(self) -> None:
        from types import SimpleNamespace

        from app.services.finance.domains.detection.analyst.activity import (
            _anomalies_section,
        )

        section = _anomalies_section(
            SimpleNamespace(
                new_insights=[
                    FinanceInsight(
                        id=12,
                        owner_user_id=OWNER,
                        insight_type="large_transaction",
                        severity="warning",
                        title="Large charge",
                        body=None,
                        dedup_key="a",
                        resolution="under_review",
                        resolution_note="Called the bank.",
                    ),
                    FinanceInsight(
                        id=13,
                        owner_user_id=OWNER,
                        insight_type="fee_charged",
                        severity="info",
                        title="Fee charged",
                        body=None,
                        dedup_key="b",
                    ),
                ]
            )
        )
        lines = section.splitlines()
        assert any(
            "(insight 12)" in line and "Under review: Called the bank." in line
            for line in lines
        )
        assert any("(insight 13)" in line for line in lines)
