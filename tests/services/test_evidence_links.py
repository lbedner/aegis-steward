"""Which paper answers which ask, in both directions at once.

Evidence and answers are many-to-many both ways, and a foreign key in
either direction forces a lie. One bank statement satisfies three
separate asks - balance, account number, where it is held. One ask
("proof of gross income") needs three separate letters.

Before this, ``RequestItem.document_id`` was a single nullable column.
The first direction worked by accident, because three items can each
point at the same document. The second was impossible: one slot, and
the second and third letters had nowhere to go.

A link also carries the PAGE, because a statement answers the ask on
page 2 and not on the other eight, and a NOTE saying how it satisfies -
which is the part somebody rereading this in six months needs and the
row itself cannot infer.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession


async def _renewal(db: AsyncSession) -> tuple[int, int, list[Any]]:
    """A matter with a request and three asks on it."""
    from app.services.matters.matters import MatterService
    from app.services.matters.requests import RequestService

    matter = await MatterService(db).open(title="Renewal", reference="R-1")
    requests = RequestService(db)
    request = await requests.record(
        matter_id=matter.id,
        items=[
            {"asked": "Proof of gross income", "kind": "figure"},
            {"asked": "Current account balance", "kind": "figure"},
            {"asked": "Where the account is held"},
        ],
    )
    return matter.id, request.id, list(await requests.items(request.id))


async def _paper(db: AsyncSession, title: str) -> Any:
    from app.services.documents.models import Document

    document = Document(
        title=title, kind="statement", storage_key=title, content_hash=title
    )
    db.add(document)
    await db.flush()
    return document


class TestOneDocumentAnsweringSeveralAsks:
    @pytest.mark.asyncio
    async def test_one_statement_satisfies_three_items(
        self, async_db_session: AsyncSession
    ) -> None:
        """ST-07's gate, first half."""
        from app.services.matters.evidence import link, satisfied_by

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")

        for item in items:
            await link(async_db_session, item.id, document_id=statement.id, page=2)
        await async_db_session.flush()

        for item in items:
            found = await satisfied_by(async_db_session, item.id)
            assert [one.document_id for one in found] == [statement.id]
            assert found[0].page == 2

    @pytest.mark.asyncio
    async def test_removing_it_reopens_every_one_of_them(
        self, async_db_session: AsyncSession
    ) -> None:
        """ST-07's gate, second half. An item whose only evidence has
        been taken away is not answered."""
        from app.services.matters.evidence import link, unlink
        from app.services.matters.requests import RequestService

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")
        for item in items:
            await link(async_db_session, item.id, document_id=statement.id)
        await async_db_session.flush()

        requests = RequestService(async_db_session)
        assert {(await requests.item(i.id)).status for i in items} == {"satisfied"}

        for item in items:
            await unlink(async_db_session, item.id, document_id=statement.id)
        await async_db_session.flush()

        assert {(await requests.item(i.id)).status for i in items} == {"needed"}


class TestOneAskNeedingSeveralDocuments:
    @pytest.mark.asyncio
    async def test_three_letters_answer_one_ask(
        self, async_db_session: AsyncSession
    ) -> None:
        """The direction the single column made impossible."""
        from app.services.matters.evidence import link, satisfied_by

        _matter, _request, items = await _renewal(async_db_session)
        income = items[0]
        titles = ["Pension letter.pdf", "Bank statement.pdf", "Award notice.pdf"]
        papers = [await _paper(async_db_session, t) for t in titles]

        for paper in papers:
            await link(async_db_session, income.id, document_id=paper.id)
        await async_db_session.flush()

        found = await satisfied_by(async_db_session, income.id)
        assert sorted(one.document_id for one in found) == sorted(p.id for p in papers)

    @pytest.mark.asyncio
    async def test_it_stays_answered_until_the_last_one_goes(
        self, async_db_session: AsyncSession
    ) -> None:
        """Taking one of three away leaves two, and two is still an
        answer. Reopening on the first removal would be a lie."""
        from app.services.matters.evidence import link, unlink
        from app.services.matters.requests import RequestService

        _matter, _request, items = await _renewal(async_db_session)
        income = items[0]
        papers = [await _paper(async_db_session, f"{n}.pdf") for n in "abc"]
        for paper in papers:
            await link(async_db_session, income.id, document_id=paper.id)
        await async_db_session.flush()

        requests = RequestService(async_db_session)
        await unlink(async_db_session, income.id, document_id=papers[0].id)
        await async_db_session.flush()
        assert (await requests.item(income.id)).status == "satisfied"

        for paper in papers[1:]:
            await unlink(async_db_session, income.id, document_id=paper.id)
        await async_db_session.flush()
        assert (await requests.item(income.id)).status == "needed"


class TestTheOtherDirection:
    @pytest.mark.asyncio
    async def test_a_document_says_what_it_satisfies(
        self, async_db_session: AsyncSession
    ) -> None:
        """Same relation read the other way: the document view shows
        what it answers, the request view shows what answers it."""
        from app.services.matters.evidence import answers_for, link

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")
        await link(async_db_session, items[0].id, document_id=statement.id)
        await link(async_db_session, items[1].id, document_id=statement.id)
        await async_db_session.flush()

        found = await answers_for(async_db_session, statement.id)
        assert sorted(one.request_item_id for one in found) == sorted(
            [items[0].id, items[1].id]
        )


class TestWhatALinkMustCarry:
    @pytest.mark.asyncio
    async def test_a_note_says_how_it_satisfies(
        self, async_db_session: AsyncSession
    ) -> None:
        """The part somebody rereading this in six months needs and the
        row cannot infer."""
        from app.services.matters.evidence import link, satisfied_by

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")
        await link(
            async_db_session,
            items[0].id,
            document_id=statement.id,
            page=2,
            note="Net benefit line, bottom of the summary",
        )
        await async_db_session.flush()

        found = await satisfied_by(async_db_session, items[0].id)
        assert found[0].note == "Net benefit line, bottom of the summary"

    @pytest.mark.asyncio
    async def test_a_fact_can_be_the_evidence_instead(
        self, async_db_session: AsyncSession
    ) -> None:
        """A figure already recorded with its provenance answers a
        "proof of income" ask as well as the letter does."""
        from app.services.matters.evidence import link, satisfied_by
        from app.services.matters.facts import FactService
        from app.services.matters.service import PartyService

        _matter, _request, items = await _renewal(async_db_session)
        party = await PartyService(async_db_session).create(
            name="Subject Testcase", kind="person"
        )
        letter = await _paper(async_db_session, "NYSLRS statement.pdf")
        fact = await FactService(async_db_session).record(
            subject_party_id=party.id,
            attribute="gross_income",
            provenance="document",
            document_id=letter.id,
            page=1,
            value_cents=100493,
            period="month",
        )
        await link(async_db_session, items[0].id, fact_id=fact.id)
        await async_db_session.flush()

        found = await satisfied_by(async_db_session, items[0].id)
        assert found[0].fact_id == fact.id
        assert found[0].document_id is None

    @pytest.mark.asyncio
    async def test_a_link_to_nothing_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        """Exactly one of document or fact. A link naming neither points
        at no evidence, and one naming both cannot say which."""
        from app.services.matters.evidence import link

        _matter, _request, items = await _renewal(async_db_session)
        with pytest.raises(ValueError, match="document or a fact"):
            await link(async_db_session, items[0].id)

    @pytest.mark.asyncio
    async def test_a_link_to_both_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.evidence import link

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")
        with pytest.raises(ValueError, match="document or a fact"):
            await link(
                async_db_session, items[0].id, document_id=statement.id, fact_id=1
            )

    @pytest.mark.asyncio
    async def test_linking_the_same_paper_twice_is_one_link(
        self, async_db_session: AsyncSession
    ) -> None:
        """Re-reading a document, or two people filing the same answer,
        must not double it."""
        from app.services.matters.evidence import link, satisfied_by

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")
        await link(async_db_session, items[0].id, document_id=statement.id, page=2)
        await link(async_db_session, items[0].id, document_id=statement.id, page=2)
        await async_db_session.flush()

        assert len(await satisfied_by(async_db_session, items[0].id)) == 1

    @pytest.mark.asyncio
    async def test_the_same_paper_on_two_pages_is_two_links(
        self, async_db_session: AsyncSession
    ) -> None:
        """A nine-page statement can answer one ask twice over, and
        which page is the whole point of recording it."""
        from app.services.matters.evidence import link, satisfied_by

        _matter, _request, items = await _renewal(async_db_session)
        statement = await _paper(async_db_session, "Statement.pdf")
        await link(async_db_session, items[0].id, document_id=statement.id, page=2)
        await link(async_db_session, items[0].id, document_id=statement.id, page=7)
        await async_db_session.flush()

        assert len(await satisfied_by(async_db_session, items[0].id)) == 2

    @pytest.mark.asyncio
    async def test_an_ask_that_does_not_exist_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.evidence import link

        with pytest.raises(ValueError, match="999999"):
            await link(async_db_session, 999999, document_id=1)
