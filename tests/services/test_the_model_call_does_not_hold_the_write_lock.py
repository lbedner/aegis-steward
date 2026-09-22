"""A model call must not be inside a database write transaction.

SQLite has one writer at a time, and ``core.db`` opens every transaction
with ``BEGIN IMMEDIATE`` - deliberately, so ``busy_timeout`` applies (a
deferred BEGIN fails its lock upgrade instantly instead of waiting). The
consequence is that the FIRST statement of a transaction takes the write
lock and holds it until commit.

``finance_analyst_note_job`` opens one session, reads the owners, calls
the model for each, and commits at the end - so the write lock is held
across every ``agent.run``. On 2026-09-20 at 02:30 that produced two
errors at once: a webserver request died on ``BEGIN IMMEDIATE`` with
"database is locked", and the scheduler's own ``record_usage`` - another
session in the same process - could not get the lock its own job was
holding.

The engine here is shaped like production rather than like the suite's
shared fixture, which emits a plain ``BEGIN`` and so cannot show this at
all. The "other writer" runs INSIDE the fake model call, which is what
makes the test deterministic: no sleeps, no races, the contention is
exactly where the bug is.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SQLITE_BUSY_TIMEOUT_MS
from app.services.ai.domains.chat.agent_loader import invalidate_agent_cache
from app.services.finance.domains.detection import analyst
from app.services.finance.domains.detection.analyst.deep_dive import run_deep_dive
from app.services.finance.models import FinanceInsight
from app.services.finance.seeds import demo_seed
from tests._sqlite import IMPATIENT_BUSY_TIMEOUT_MS

OWNER = 1
HEADLINE = "One category moved. Nothing else did."

# Production waits 30s before giving up. A test that reproduced the wait
# would take 30s to fail, so it waits a beat instead - the question is
# whether the lock is HELD, not how patiently the loser waits.


@pytest.fixture(autouse=True)
def _clean_agent_cache():
    """``resolve_agent`` memoizes per process; tests must not inherit rows."""
    invalidate_agent_cache()
    yield
    invalidate_agent_cache()


def _opens_new(maker: Any) -> Any:
    """Production's session factory, over a test engine: a NEW session
    per open, committed on the way out, exactly as ``get_async_session``
    does. What the code under test is handed instead of an open one."""

    @asynccontextmanager
    async def _open():
        async with maker() as session:
            yield session
            await session.commit()

    return _open


async def _a_second_writer(maker: Any, dedup_key: str) -> Exception | None:
    """Another writer, from inside the fake model call - the exact moment
    the app is waiting on a model. Returns its failure rather than
    raising it, because the callers here are deliberately total and
    would swallow it into "skipped" otherwise.

    One row, one session, one commit: the cheapest thing in the app that
    needs the write lock, so the only reason it can fail is that
    somebody else is holding it.
    """
    try:
        async with maker() as other:
            other.add(
                FinanceInsight(
                    owner_user_id=OWNER,
                    insight_type="fee",
                    severity="info",
                    title="Written while the model was thinking",
                    dedup_key=dedup_key,
                    data={},
                    metadata_={},
                )
            )
            await other.commit()
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        return exc
    return None


def test_production_waits_far_longer_than_this_test_does() -> None:
    """Pins the relationship, so the short timeout reads as a test
    convenience rather than a different behaviour being tested."""
    assert SQLITE_BUSY_TIMEOUT_MS > IMPATIENT_BUSY_TIMEOUT_MS


@pytest.mark.asyncio
async def test_another_writer_can_work_while_the_model_is_thinking(
    impatient_engine, monkeypatch
) -> None:
    maker = async_sessionmaker(
        impatient_engine, class_=AsyncSession, expire_on_commit=False
    )

    # Everything that opens its own session lands on this engine.
    monkeypatch.setattr(
        "app.services.finance.jobs.get_async_session", _opens_new(maker)
    )
    monkeypatch.setattr(
        "app.services.ai.domains.chat.module_context.get_async_session",
        _opens_new(maker),
    )

    async with maker() as setup:
        await demo_seed.seed_demo(setup, owner_user_id=OWNER)
        setup.add(analyst.MemoryModule(**analyst.snapshot_module_definition()))
        setup.add(analyst.Agent(**analyst.analyst_agent_definition()))
        await setup.commit()

    other_writer: dict[str, Any] = {}

    async def _respond(messages: Any, info: Any) -> ModelResponse:
        """Called from inside ``agent.run`` - the exact moment the job is
        waiting on a model. Anything else in the app that wants to write
        is waiting here too, so this is where to ask whether it can.

        Async, so the other writer is simply awaited: no sleeps, no
        second thread, no race. Its failure is recorded rather than
        raised, because ``run_analyst_note`` is deliberately total and
        would swallow it into "note skipped" otherwise.
        """
        other_writer["error"] = await _a_second_writer(maker, "concurrent-1")

        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool.name, {"headline": HEADLINE})])

    def _model_for(config: Any, settings: Any) -> tuple[Any, str]:
        return FunctionModel(_respond), "test-model"

    monkeypatch.setattr("app.services.ai.domains.llm.providers.model_for", _model_for)
    monkeypatch.setattr(analyst.note, "current_date", lambda: date(2026, 7, 20))

    from app.services.finance.jobs import finance_analyst_note_job

    await finance_analyst_note_job()

    assert "error" in other_writer, "the fake model was never called"
    # The whole point. If the job is holding the write lock across
    # agent.run, this is "database is locked" on BEGIN IMMEDIATE.
    assert other_writer["error"] is None, (
        "another writer could not work while the model was running: "
        f"{other_writer['error']!r}"
    )


SITUATION = "You are short before the 12th."


@pytest.mark.asyncio
async def test_another_writer_can_work_while_the_deep_dive_is_thinking(
    impatient_engine, monkeypatch
) -> None:
    """The deep dive is the nightly note's shape without a job around it:
    it is called straight from a request, and the session it holds while
    it waits on the model is the application's one write lock all the
    same. Same fake model, same writer inside it, same question."""
    maker = async_sessionmaker(
        impatient_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with maker() as setup:
        await demo_seed.seed_demo(setup, owner_user_id=OWNER)
        setup.add(analyst.MemoryModule(**analyst.snapshot_module_definition()))
        setup.add(analyst.Agent(**analyst.seeds.deep_dive_agent_definition()))
        await setup.commit()

    other_writer: dict[str, Any] = {}

    async def _respond(messages: Any, info: Any) -> ModelResponse:
        """Inside ``agent.run``: the moment the review is waiting on the
        model, and the moment every other writer is waiting on it."""
        other_writer["error"] = await _a_second_writer(maker, "concurrent-2")

        tool = info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool.name, {"situation": SITUATION})])

    def _model_for(config: Any, settings: Any) -> tuple[Any, str]:
        return FunctionModel(_respond), "test-model"

    monkeypatch.setattr("app.services.ai.domains.llm.providers.model_for", _model_for)

    dive = await run_deep_dive(
        _opens_new(maker), owner_user_id=OWNER, today=date(2026, 7, 20)
    )

    assert "error" in other_writer, "the fake model was never called"
    assert other_writer["error"] is None, (
        "another writer could not work while the model was running: "
        f"{other_writer['error']!r}"
    )
    assert dive is not None, "the deep dive wrote nothing"


async def test_another_writer_can_work_while_the_letter_is_being_read(
    impatient_engine,
) -> None:
    """A letter's demands are read by a model, and the reading used to
    hold one session from the first "has this been asked already?" all
    the way past ``read_letter`` to the card it proposes - so every page
    the worker read was a write lock nobody else could take (#211).
    """
    from app.services.documents.domains.reading.letters import LetterReading, ReadItem
    from app.services.documents.domains.reading.proposals import propose_reading
    from app.services.documents.models import DocumentPage
    from app.services.documents.service import DocumentService
    from app.services.matters.matters import MatterService
    from app.services.matters.models import matter_tag

    maker = async_sessionmaker(
        impatient_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with maker() as setup:
        matter = await MatterService(setup).open(title="Renewal", reference="MA-1")
        document = await DocumentService(setup).ingest(
            b"%PDF-1.4 renewal", title="Request.pdf", kind="letter"
        )
        await setup.flush()
        document_id = int(document.id)
        setup.add(
            DocumentPage(
                document_id=document_id,
                page_number=1,
                status="read",
                method="text_layer",
                text="Proof of GROSS monthly",
            )
        )
        # Only a letter filed on a matter is read by the model at all.
        await DocumentService(setup).tag(document_id, matter_tag(int(matter.id)))
        await setup.commit()

    other_writer: dict[str, Any] = {}

    async def _read_letter(pages: Any) -> Any:
        other_writer["error"] = await _a_second_writer(maker, "reading-1")
        return LetterReading(
            items=[
                ReadItem(
                    asked="Send proof",
                    kind="figure",
                    page=1,
                    quote="Proof of GROSS monthly",
                )
            ]
        )

    card = await propose_reading(
        _opens_new(maker), document_id, read_letter=_read_letter
    )

    assert "error" in other_writer, "the fake letter reader was never called"
    assert other_writer["error"] is None, (
        "another writer could not work while the letter was being read: "
        f"{other_writer['error']!r}"
    )
    assert card is not None, "the reading proposed nothing"


@pytest.mark.asyncio
async def test_another_writer_can_work_while_a_conversation_is_scored(
    impatient_engine, monkeypatch
) -> None:
    """Sentiment scoring reads a batch, then calls the model once per
    conversation. It used to keep the caller's session for all of it and
    commit that session mid-batch to get the lock back - which released
    the lock by committing work the caller never asked to commit. The
    batch now reads with one session and writes each verdict with
    another, and holds neither across the model.
    """
    from app.models.conversation import Conversation, ConversationMessage
    import app.services.ai.domains.chat.sentiment as sentiment_module
    from app.services.ai.models.sentiment import SentimentAnalysis

    maker = async_sessionmaker(
        impatient_engine, class_=AsyncSession, expire_on_commit=False
    )

    monkeypatch.setattr(sentiment_module, "get_async_session", _opens_new(maker))

    async with maker() as setup:
        setup.add(Conversation(id="c1", user_id="u1", title="c1"))
        setup.add(
            ConversationMessage(conversation_id="c1", role="user", content="hello")
        )
        await setup.commit()

    other_writer: dict[str, Any] = {}

    async def _fake_llm_score(transcript: str) -> dict[str, Any]:
        other_writer["error"] = await _a_second_writer(maker, "sentiment-1")
        return {
            "overall_sentiment": "positive",
            "overall_score": 0.8,
            "assistant_performance": "good",
            "issues": [],
            "summary": "A pleasant chat.",
        }

    monkeypatch.setattr(sentiment_module, "_llm_score", _fake_llm_score)

    counts = await sentiment_module.score_unscored_conversations()

    assert "error" in other_writer, "the fake model was never called"
    assert other_writer["error"] is None, (
        "another writer could not work while the conversation was scored: "
        f"{other_writer['error']!r}"
    )
    assert counts["scored"] == 1
    async with maker() as check:
        verdict = (await check.exec(select(SentimentAnalysis))).first()
    assert verdict is not None, "the verdict was never written"


class TestReadingAPageDoesNotHoldTheLock:
    """A scan is minutes of model calls. One transaction across all of
    them holds SQLite's single writer for the whole document, and two
    readings of one file deadlock.

    ``pages.py`` already commits each page as it lands - this pins that,
    because the bug it prevents is invisible until somebody is watching
    the app freeze, and the ticket that listed this site had it down as
    unfixed a week after it was fixed.
    """

    @pytest.mark.asyncio
    async def test_another_writer_can_work_while_a_page_is_being_read(
        self, impatient_engine: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        from app.core.storage import FilesystemStorage, set_storage
        from app.services.documents.domains.extraction import jobs, pages
        from app.services.documents.models import Document
        from app.services.documents.service import DocumentService
        from tests._pdf import pdf_bytes

        set_storage(FilesystemStorage(tmp_path / "storage"))
        maker = async_sessionmaker(
            impatient_engine, class_=AsyncSession, expire_on_commit=False
        )

        monkeypatch.setattr(jobs, "get_async_session", _opens_new(maker))

        async with maker() as db:
            paper = await DocumentService(db).ingest(
                pdf_bytes(["a scan"]),
                title="scan.pdf",
                kind="letter",
                media_type="application/pdf",
            )
            await db.commit()
            document_id = int(paper.id)

        other_writer: dict[str, Any] = {}

        async def _vision(image: bytes, media_type: str) -> tuple[str, str]:
            """The second writer runs INSIDE the read: contention exactly
            where the bug would be, with no sleeps and no races."""
            try:
                async with maker() as other:
                    other.add(
                        Document(
                            title="Filed while the page was being read",
                            kind="other",
                            storage_key="sha256/aa/bb/" + "c" * 64,
                            content_hash="c" * 64,
                            media_type="text/plain",
                        )
                    )
                    await other.commit()
                other_writer["error"] = None
            except Exception as exc:  # noqa: BLE001 - reported, not handled
                other_writer["error"] = exc
            return "what the page said", "test-vision"

        async def _reader() -> Any:
            return _vision

        monkeypatch.setattr(jobs, "vision_reader", _reader)
        # No text layer and no OCR, so the page goes to the model.
        monkeypatch.setattr(pages, "MIN_TEXT_CHARS", 10**9)

        async def _no_ocr(page: Any, image: Any) -> bool:
            return False

        monkeypatch.setattr(pages, "_read_with_ocr", _no_ocr)

        try:
            await jobs.run_extraction(
                document_id, owner_user_id=None, force=True, report=lambda _label: None
            )
        finally:
            set_storage(None)

        assert "error" in other_writer, "the fake vision reader was never called"
        assert other_writer["error"] is None, (
            "another writer could not work while a page was being read: "
            f"{other_writer['error']!r}"
        )
