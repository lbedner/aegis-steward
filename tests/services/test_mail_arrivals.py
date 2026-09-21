"""What came in: one answer for the page and for the chat (MI-06).

Mail that arrives, reads itself and proposes cards is still a pile if
the only way to see it is the approvals queue, which says what is
WAITING rather than what ARRIVED. The intake rows say, per message:
when, from whom, what it became - a letter, its attachments, a card -
and, when nothing, why. The page and the assistant read the same rows
so they cannot disagree.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.mail import ingest
from app.services.mail.arrivals import arrivals
from app.services.matters.service import PartyService
from tests._mbox import PDF, eml_bytes, mbox_bytes, message


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


class TestWhatCameIn:
    """The milestone's gate: a statement with an attachment, a letter with
    none, and a newsletter - all three shown, each saying what it became."""

    @pytest.mark.asyncio
    async def test_three_mails_and_what_each_became(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        db = async_db_session
        delta = await PartyService(db).create(
            name="Delta Dental",
            kind="organization",
            contact={"email": "claims@deltadentalins.com"},
        )
        data = mbox_bytes(
            [
                message(
                    sender="Delta Dental <claims@deltadentalins.com>",
                    subject="Your statement",
                    attachments=[("statement.pdf", "application/pdf", PDF)],
                ),
                message(
                    sender="County <intake@county.example>", subject="Renewal notice"
                ),
                message(
                    sender="News <news@shop.example>", subject="Weekly deals", body=""
                ),
            ]
        )
        await ingest.ingest_mail(open_session, data=data, file_name="takeout.mbox")

        [day] = await arrivals(db, owner_user_id=None)
        rows = {row.subject: row for row in day.messages}
        assert set(rows) == {"Your statement", "Renewal notice", "Weekly deals"}

        statement = rows["Your statement"]
        assert statement.sender == "Delta Dental"
        assert statement.party == {"id": delta.id, "name": "Delta Dental"}
        assert statement.letter_id is not None
        assert [a.filename for a in statement.attachments] == ["statement.pdf"]
        assert statement.card is None  # known sender: nothing to ask
        assert statement.became == "letter + 1 attachment, filed under Delta Dental"

        notice = rows["Renewal notice"]
        assert notice.sender == "County" and notice.party is None
        assert notice.card == "pending"
        assert notice.became == "letter; a card offers to add County"

        deals = rows["Weekly deals"]
        assert deals.letter_id is None and deals.attachments == []
        assert deals.became == "nothing: empty message, no attachments"

    @pytest.mark.asyncio
    async def test_days_are_newest_first_and_only_this_owners(
        self, async_db_session: AsyncSession, open_session: Any, dispatched: list[int]
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from app.services.mail.models import MailMessage

        db = async_db_session
        await ingest.ingest_mail(
            open_session, data=eml_bytes(message(subject="today")), file_name="a.eml"
        )
        await ingest.ingest_mail(
            open_session,
            data=eml_bytes(message(subject="yesterday", message_id="<y@x>")),
            file_name="b.eml",
        )
        old = next(
            r
            for r in (await db.exec(__import__("sqlmodel").select(MailMessage))).all()
            if r.subject == "yesterday"
        )
        old.created_at = datetime.now(UTC) - timedelta(days=1)
        db.add(old)
        await db.flush()

        days = await arrivals(db, owner_user_id=None)
        assert [d.messages[0].subject for d in days] == ["today", "yesterday"]
        assert days[0].date > days[1].date
        assert await arrivals(db, owner_user_id=99) == []
