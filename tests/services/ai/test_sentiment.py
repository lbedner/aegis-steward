"""Tests for batch conversation sentiment scoring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import OpenSession
from app.models.conversation import Conversation, ConversationMessage
import app.services.ai.domains.chat.sentiment as sentiment_module
from app.services.ai.domains.chat.sentiment import (
    score_unscored_conversations,
    sentiment_stats,
)
from app.services.ai.jobs import analyze_sentiment_job
from app.services.ai.models.sentiment import SentimentAnalysis
from tests._session import opens

_GOOD_VERDICT: dict[str, Any] = {
    "overall_sentiment": "positive",
    "overall_score": 0.8,
    "assistant_performance": "good",
    "issues": [],
    "summary": "A pleasant chat.",
}


@pytest.fixture
def session(async_db_session: AsyncSession) -> AsyncSession:
    """The root conftest's transactional async session (rolled back per
    test). A bare local engine cannot create this project's schema-qualified
    tables (finance, scheduler, ...)."""
    return async_db_session


@pytest.fixture
def open_session(async_db_session: AsyncSession) -> OpenSession:
    """``score_unscored_conversations`` takes a way to OPEN a database.
    Tests hand back the per-test session and never commit it."""
    return opens(async_db_session)


async def _add_conversation(
    session: AsyncSession, conv_id: str, *, with_message: bool = True
) -> None:
    session.add(Conversation(id=conv_id, user_id="u1", title=conv_id))
    if with_message:
        session.add(
            ConversationMessage(
                conversation_id=conv_id, role="user", content="hello there"
            )
        )
    await session.commit()


def _stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    verdict: dict[str, Any] | None = None,
    fail_for: str | None = None,
) -> dict[str, int]:
    """Replace the LLM seam; returns a call counter."""
    calls = {"count": 0}
    current_verdict = verdict or _GOOD_VERDICT

    async def fake_llm_score(transcript: str) -> dict[str, Any]:
        calls["count"] += 1
        if fail_for and fail_for in transcript:
            raise RuntimeError("model exploded")
        return dict(current_verdict)

    monkeypatch.setattr(sentiment_module, "_llm_score", fake_llm_score)
    return calls


class TestScoreOnce:
    async def test_scores_unscored_conversations_exactly_once(
        self,
        session: AsyncSession,
        open_session: OpenSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_llm(monkeypatch)
        await _add_conversation(session, "c1")
        await _add_conversation(session, "c2")

        first = await score_unscored_conversations(open_session=open_session)
        assert first["scored"] == 2

        second = await score_unscored_conversations(open_session=open_session)
        assert second["scored"] == 0

        result = await session.exec(select(SentimentAnalysis))
        rows = list(result.all())
        assert len(rows) == 2
        assert {row.overall_sentiment for row in rows} == {"positive"}

    async def test_conversation_without_messages_is_skipped(
        self,
        session: AsyncSession,
        open_session: OpenSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _stub_llm(monkeypatch)
        await _add_conversation(session, "empty", with_message=False)

        counts = await score_unscored_conversations(open_session=open_session)

        assert counts == {"scored": 0, "skipped": 1, "failed": 0}
        assert calls["count"] == 0


class TestFailureIsolation:
    async def test_one_failure_does_not_abort_the_batch(
        self,
        session: AsyncSession,
        open_session: OpenSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_llm(monkeypatch, fail_for="poison")
        await _add_conversation(session, "good1")
        session.add(Conversation(id="bad", user_id="u1", title="bad"))
        session.add(
            ConversationMessage(
                conversation_id="bad", role="user", content="poison message"
            )
        )
        await session.commit()

        counts = await score_unscored_conversations(open_session=open_session)

        assert counts["scored"] == 1
        assert counts["failed"] == 1
        # The failed conversation stays unscored for a later retry.
        result = await session.exec(select(SentimentAnalysis))
        assert [row.conversation_id for row in result.all()] == ["good1"]

    async def test_invalid_verdict_counts_as_failure(
        self,
        session: AsyncSession,
        open_session: OpenSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _stub_llm(
            monkeypatch,
            verdict={
                "overall_sentiment": "ecstatic",  # not a valid value
                "overall_score": 5,
                "assistant_performance": "good",
            },
        )
        await _add_conversation(session, "c1")

        counts = await score_unscored_conversations(open_session=open_session)

        assert counts == {"scored": 0, "skipped": 0, "failed": 1}
        result = await session.exec(select(SentimentAnalysis))
        assert result.first() is None


class TestSentimentStats:
    async def test_empty_state_is_zero_filled(self, session: AsyncSession) -> None:
        stats = await sentiment_stats(session=session)

        assert stats["total"] == 0
        assert stats["distribution"] == {
            "positive": 0,
            "neutral": 0,
            "negative": 0,
            "frustrated": 0,
        }
        assert stats["recent_negatives"] == []
        assert stats["enabled"] is False

    async def test_distribution_average_and_negatives(
        self, session: AsyncSession
    ) -> None:
        for conv_id, sentiment_value, score, performance in [
            ("c1", "positive", 0.9, "good"),
            ("c2", "positive", 0.7, "good"),
            ("c3", "frustrated", -0.8, "poor"),
        ]:
            await _add_conversation(session, conv_id)
            session.add(
                SentimentAnalysis(
                    conversation_id=conv_id,
                    overall_sentiment=sentiment_value,
                    overall_score=score,
                    assistant_performance=performance,
                    summary=f"summary of {conv_id}",
                )
            )
        await session.commit()

        stats = await sentiment_stats(session=session)

        assert stats["total"] == 3
        assert stats["distribution"]["positive"] == 2
        assert stats["distribution"]["frustrated"] == 1
        assert stats["performance"] == {"good": 2, "acceptable": 0, "poor": 1}
        assert stats["average_score"] == round((0.9 + 0.7 - 0.8) / 3, 3)
        negatives = stats["recent_negatives"]
        assert len(negatives) == 1
        assert negatives[0]["conversation_id"] == "c3"
        assert negatives[0]["summary"] == "summary of c3"


class TestJobGate:
    async def test_job_is_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The scheduled job never touches the scorer until enabled."""
        from app.core.config import settings

        assert settings.AI_SENTIMENT_ENABLED is False

        touched = {"ran": False}

        async def fake_batch(**kwargs: Any) -> dict[str, int]:
            touched["ran"] = True
            return {"scored": 0, "skipped": 0, "failed": 0}

        monkeypatch.setattr(
            sentiment_module, "score_unscored_conversations", fake_batch
        )

        await analyze_sentiment_job()

        assert touched["ran"] is False

    async def test_enabled_job_runs_the_batch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "AI_SENTIMENT_ENABLED", True)

        touched = {"ran": False}

        async def fake_batch(**kwargs: Any) -> dict[str, int]:
            touched["ran"] = True
            return {"scored": 0, "skipped": 0, "failed": 0}

        monkeypatch.setattr(
            sentiment_module, "score_unscored_conversations", fake_batch
        )

        await analyze_sentiment_job()

        assert touched["ran"] is True


class TestTheDatabaseIsNotHeldAcrossTheModelCall:
    """SQLite has one writer, and every transaction here begins
    IMMEDIATE, so an open session is the application-wide write lock.
    The batch used to keep the caller's session for the whole run and
    commit it mid-batch to get the lock back - which bought the lock by
    committing work the caller never asked to commit (#211).
    """

    async def test_the_batch_read_and_each_verdict_are_separate_sessions(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened = 0
        live = 0

        @asynccontextmanager
        async def _counting_open() -> AsyncIterator[AsyncSession]:
            nonlocal opened, live
            opened += 1
            live += 1
            try:
                yield session
            finally:
                live -= 1

        held: list[int] = []

        async def fake_llm_score(transcript: str) -> dict[str, Any]:
            held.append(live)
            return dict(_GOOD_VERDICT)

        monkeypatch.setattr(sentiment_module, "_llm_score", fake_llm_score)
        await _add_conversation(session, "c1")

        counts = await sentiment_module.score_unscored_conversations(
            open_session=_counting_open
        )

        assert counts["scored"] == 1
        assert held == [0], "a session was open while the model was being asked"
        # One to read the batch, one to write the verdict, nothing in between.
        assert opened == 2
