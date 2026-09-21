"""What a day's arrivals answer in each other (MI-07).

Each document proposes what IT says - deliberately narrow, because a
reading that guesses beyond its own page is the failure the whole
surface exists to avoid. But a day's post is not unrelated pages: the
county's letter asks for three things, and the statement beside it
answers one of them. The joins BETWEEN arrivals are what a person does
by hand at a kitchen table, and nothing proposed them.

Nothing here is a model call. A join cites the page and the line the
figure was read from, and the ask it answers by its own wording -
"the model thought so" is not evidence.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.joins import propose_joins
from app.services.finance.models.changes import FinancePendingChange
from app.services.matters.matters import MatterService
from app.services.matters.models.core import matter_tag
from app.services.matters.requests import RequestService
from app.services.matters.service import PartyService

STATEMENT = "page 1: Balance as of 09/07/26: $7,857.27"


async def _cards(db: AsyncSession, change_type: str) -> list[Any]:
    return [
        row
        for row in (await db.exec(select(FinancePendingChange))).all()
        if row.change_type == change_type
    ]


async def _matter_with_an_ask(
    db: AsyncSession, *, ask: str | None, asked: str = "Proof of account balance"
) -> tuple[int, int, int]:
    """A matter, its subject, and one open ask on it."""
    party = await PartyService(db).create(name="James Bedner", kind="person")
    matter = await MatterService(db).open(
        reference="MA-JOIN-1", title="Renewal", subject_party_id=int(party.id)
    )
    requests = RequestService(db)
    letter = await requests.record(
        matter_id=int(matter.id), received_on=date(2026, 9, 1)
    )
    item = await requests.add_item(int(letter.id), asked=asked, ask=ask)
    await db.flush()
    return int(matter.id), int(party.id), int(item.id)


async def _statement_on(
    db: AsyncSession,
    matter_id: int,
    party_id: int,
    *,
    attribute: str = "account_balance",
    bytes_: bytes = b"statement",
) -> int:
    """A statement filed on the matter, with the figure it states already
    proposed the way ``propose_figure`` proposes it."""
    from app.services.documents.service import DocumentService
    from app.services.finance.domains.writes.queue import propose

    documents = DocumentService(db)
    # Distinct bytes per statement: the store dedupes by content, so two
    # identical ones are one document.
    paper = await documents.ingest(
        bytes_, title="September statement", kind="statement"
    )
    await documents.tag(int(paper.id), matter_tag(matter_id))
    await propose(
        db,
        "fact.record",
        {
            "subject_party_id": party_id,
            "attribute": attribute,
            "value_cents": 785727,
            "as_of": "2026-09-07",
            "provenance": "document",
            "document_id": int(paper.id),
            "page": 1,
            "source_note": STATEMENT,
        },
        proposed_by_agent="reading",
    )
    await db.flush()
    return int(paper.id)


class TestAStatementAnswersAnAsk:
    @pytest.mark.asyncio
    async def test_the_figure_it_states_links_it_to_the_ask_that_wanted_it(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        matter_id, party_id, item_id = await _matter_with_an_ask(
            db, ask="account_balance"
        )
        document_id = await _statement_on(db, matter_id, party_id)

        made = await propose_joins(db, document_ids=[document_id], owner_user_id=None)

        [card] = await _cards(db, "document.evidence_link")
        assert card.payload == {
            "document_id": document_id,
            "request_item_id": item_id,
            "page": 1,
            "because": STATEMENT,
        }
        assert card.proposed_by_agent == "reading"
        assert [c.id for c in made] == [card.id]

    @pytest.mark.asyncio
    async def test_an_ask_about_something_else_is_not_answered(
        self, async_db_session: AsyncSession
    ) -> None:
        """A balance does not answer a question about income. Linking on
        "they arrived together" is how a wrong answer gets approved."""
        db = async_db_session
        matter_id, party_id, _item = await _matter_with_an_ask(db, ask="gross_income")
        document_id = await _statement_on(db, matter_id, party_id)

        await propose_joins(db, document_ids=[document_id], owner_user_id=None)

        assert await _cards(db, "document.evidence_link") == []

    @pytest.mark.asyncio
    async def test_a_statement_nobody_asked_for_joins_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.service import DocumentService

        db = async_db_session
        loose = await DocumentService(db).ingest(
            b"x", title="Unasked", kind="statement"
        )
        await db.flush()

        await propose_joins(db, document_ids=[int(loose.id)], owner_user_id=None)

        assert await _cards(db, "document.evidence_link") == []

    @pytest.mark.asyncio
    async def test_running_again_proposes_nothing_new(
        self, async_db_session: AsyncSession
    ) -> None:
        """One pass per day over what is new. A second pass over the same
        arrival is a queue of duplicates."""
        db = async_db_session
        matter_id, party_id, _item = await _matter_with_an_ask(
            db, ask="account_balance"
        )
        document_id = await _statement_on(db, matter_id, party_id)

        await propose_joins(db, document_ids=[document_id], owner_user_id=None)
        await propose_joins(db, document_ids=[document_id], owner_user_id=None)

        assert len(await _cards(db, "document.evidence_link")) == 1

    @pytest.mark.asyncio
    async def test_an_ask_already_answered_is_left_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        matter_id, party_id, item_id = await _matter_with_an_ask(
            db, ask="account_balance"
        )
        await RequestService(db).mark(item_id, "satisfied")
        document_id = await _statement_on(db, matter_id, party_id)

        await propose_joins(db, document_ids=[document_id], owner_user_id=None)

        assert await _cards(db, "document.evidence_link") == []


class TestAnUnmappedAskIsNamed:
    """An ask nobody mapped is an ask no recorded figure can ever meet.
    The letter's own wording names the attribute; the card offers it."""

    @pytest.mark.asyncio
    async def test_the_asks_wording_proposes_the_attribute(
        self, async_db_session: AsyncSession
    ) -> None:
        db = async_db_session
        matter_id, party_id, item_id = await _matter_with_an_ask(
            db, ask=None, asked="Please send proof of gross income for James"
        )
        document_id = await _statement_on(db, matter_id, party_id)

        await propose_joins(db, document_ids=[document_id], owner_user_id=None)

        [card] = await _cards(db, "ask.amend")
        assert card.payload["item_id"] == item_id
        assert card.payload["ask"] == "gross_income"
        assert "gross income" in card.payload["reason"].lower()

    @pytest.mark.asyncio
    async def test_wording_that_names_two_things_names_neither(
        self, async_db_session: AsyncSession
    ) -> None:
        """ "Gross income and account balance" is two asks written as one.
        Guessing which one the ask is about is the guess this refuses."""
        db = async_db_session
        matter_id, party_id, _item = await _matter_with_an_ask(
            db, ask=None, asked="Gross income and account balance for the household"
        )
        document_id = await _statement_on(db, matter_id, party_id)

        await propose_joins(db, document_ids=[document_id], owner_user_id=None)

        assert await _cards(db, "ask.amend") == []


class TestTheDailyPass:
    """One pass a day, over what is NEW. Re-reading the whole shelf
    nightly is a bill and a queue of duplicates."""

    @pytest.mark.asyncio
    async def test_it_joins_what_arrived_and_leaves_the_old_shelf_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        from datetime import timedelta

        from app.core.clock import utcnow
        from app.services.documents.domains.reading.joins import join_recent_arrivals
        from app.services.documents.models import Document

        db = async_db_session
        matter_id, party_id, item_id = await _matter_with_an_ask(
            db, ask="account_balance"
        )
        fresh = await _statement_on(db, matter_id, party_id)

        stale = await _statement_on(db, matter_id, party_id, bytes_=b"older statement")
        old = await db.get(Document, stale)
        old.received_at = utcnow() - timedelta(days=30)
        db.add(old)
        await db.flush()

        await join_recent_arrivals(db, owner_user_id=None)

        [card] = await _cards(db, "document.evidence_link")
        assert card.payload["document_id"] == fresh
        assert card.payload["request_item_id"] == item_id


class TestTheScheduledPass:
    @pytest.mark.asyncio
    async def test_the_job_opens_its_own_database_and_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scheduler fires with no request behind it, so the job owns
        its session - and a pass that proposes without committing is a
        pass nobody sees."""
        from contextlib import asynccontextmanager

        from app.services.documents.domains.reading import joins

        seen: list[str] = []

        class Session:
            async def commit(self) -> None:
                seen.append("commit")

        @asynccontextmanager
        async def _open():
            seen.append("open")
            yield Session()

        async def _pass(db: Any, *, owner_user_id: int | None) -> list[Any]:
            seen.append("joined")
            return []

        monkeypatch.setattr(joins, "get_async_session", _open)
        monkeypatch.setattr(joins, "join_recent_arrivals", _pass)

        await joins.join_arrivals_job()

        assert seen == ["open", "joined", "commit"]

    def test_the_job_is_registered_daily(self) -> None:
        """A task nobody registered never runs."""
        source = (
            __import__("pathlib").Path("app/components/scheduler/main.py").read_text()
        )
        assert "join_arrivals_job" in source
        assert 'id="join_arrivals"' in source


class TestTheGate:
    """The milestone's own gate: a letter and a statement arrive the same
    morning, and approving what the queue then holds makes the sheet
    complete. Nobody asked for any of it."""

    @pytest.mark.asyncio
    async def test_approving_the_join_answers_the_ask(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.domains.writes.queue import approve
        from app.services.matters.answers import answer_sheet

        db = async_db_session
        matter_id, party_id, item_id = await _matter_with_an_ask(
            db, ask="account_balance"
        )
        document_id = await _statement_on(db, matter_id, party_id)

        before = await answer_sheet(db, matter_id)
        assert [a["state"] for a in before["answers"]] == ["missing"]

        await propose_joins(db, document_ids=[document_id], owner_user_id=None)
        for change_type in ("fact.record", "document.evidence_link"):
            for card in await _cards(db, change_type):
                await approve(db, int(card.id))

        after = await answer_sheet(db, matter_id)
        [answer] = after["answers"]
        assert answer["state"] == "answered"
        assert answer["value"] == "$7,857.27 (Account balance)"
        assert [s["title"] for s in answer["sources"]] == ["September statement"]
