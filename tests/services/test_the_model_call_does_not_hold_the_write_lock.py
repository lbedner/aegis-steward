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
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import SQLITE_BUSY_TIMEOUT_MS
from app.services.ai.domains.chat.agent_loader import invalidate_agent_cache
from app.services.finance.domains.detection import analyst
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

    @asynccontextmanager
    async def _session():
        async with maker() as session:
            yield session

    # Everything that opens its own session lands on this engine.
    monkeypatch.setattr("app.services.finance.jobs.get_async_session", _session)
    monkeypatch.setattr(
        "app.services.ai.domains.chat.module_context.get_async_session", _session
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
        try:
            async with maker() as other:
                other.add(
                    FinanceInsight(
                        owner_user_id=OWNER,
                        insight_type="fee",
                        severity="info",
                        title="Written while the model was thinking",
                        dedup_key="concurrent-1",
                        data={},
                        metadata_={},
                    )
                )
                await other.commit()
            other_writer["error"] = None
        except Exception as exc:  # noqa: BLE001 - reported, not handled
            other_writer["error"] = exc

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
