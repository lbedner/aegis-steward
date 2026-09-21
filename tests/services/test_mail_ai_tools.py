"""The assistant's ``arrivals`` tool reads the intake page's rows (MI-06).

The tool opens its own session in production; tests point that at the
transactional test session so ingested rows are visible and rolled back.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.chat.tools import registered_tool_names
from app.services.mail import ingest
import app.services.mail.ai_tools as ai_tools
from app.services.mail.arrivals import arrivals as page_arrivals
from app.services.mail.models import MailMessage
from tests._mbox import PDF, eml_bytes, mbox_bytes, message
from tests._session import opens


@pytest.fixture(autouse=True)
def _tools_use_test_session(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_tools, "get_async_session", opens(async_db_session))


@pytest.fixture
def open_session(async_db_session: AsyncSession):
    @asynccontextmanager
    async def _open():
        yield async_db_session

    return _open


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    from app.services.documents.domains.extraction import dispatch

    asked: list[int] = []

    async def _fake(document_id: int, *, owner_user_id: Any, force: bool) -> str:
        asked.append(document_id)
        return "job"

    monkeypatch.setattr(dispatch, "start_extraction", _fake)
    return asked


def test_arrivals_registers_on_import() -> None:
    assert "arrivals" in registered_tool_names()


@pytest.mark.asyncio
async def test_arrivals_says_what_the_page_says(
    async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
) -> None:
    data = mbox_bytes(
        [
            message(
                sender="Delta Dental <claims@deltadentalins.com>",
                subject="Your statement",
                attachments=[("statement.pdf", "application/pdf", PDF)],
            ),
            message(sender="County <intake@county.example>", subject="Renewal notice"),
            message(sender="News <news@shop.example>", subject="Weekly deals", body=""),
        ]
    )
    await ingest.ingest_mail(open_session, data=data, file_name="takeout.mbox")

    [day] = (await ai_tools.arrivals())["days"]
    [page_day] = await page_arrivals(async_db_session, owner_user_id=None)

    assert day["date"] == page_day.date.isoformat()
    assert len(day["messages"]) == 3
    assert day["messages"] == [row.as_dict() for row in page_day.messages]
    assert {m["became"] for m in day["messages"]} == {
        "letter + 1 attachment; a card offers to add Delta Dental",
        "letter; a card offers to add County",
        "nothing: empty message, no attachments",
    }


@pytest.mark.asyncio
async def test_days_one_returns_the_latest_day_when_today_is_empty(
    async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
) -> None:
    await ingest.ingest_mail(
        open_session, data=eml_bytes(message(subject="yesterday")), file_name="a.eml"
    )
    row = (await async_db_session.exec(select(MailMessage))).one()
    row.created_at = datetime.now(UTC) - timedelta(days=1)
    async_db_session.add(row)
    await async_db_session.flush()

    days = (await ai_tools.arrivals(days=1))["days"]
    assert [m["subject"] for d in days for m in d["messages"]] == ["yesterday"]
    assert days[0]["date"] == row.created_at.date().isoformat()
