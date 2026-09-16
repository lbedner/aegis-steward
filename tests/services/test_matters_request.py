"""Requests: what a letter obliges you to produce, and by when.

ST-05's gate is the renewal itself - one request, a 2026-09-08 due date,
three items each markable on its own. Satisfying the power of attorney
leaves the income items open, and the request only reads satisfied when
every item does.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.matters import MatterService
from app.services.matters.requests import (
    RequestService,
    overdue,
    standing,
)

ASKED = (
    "A copy of the power of attorney naming a representative.",
    "Proof of GROSS monthly income for the IBEW and iHeart pensions.",
    "Resource values and account numbers as of 1 August 2026.",
)


async def _renewal(db: AsyncSession) -> tuple[int, int]:
    matter = await MatterService(db).open(
        title="Medicaid renewal", reference="MA258760XX"
    )
    request = await RequestService(db).record(
        matter_id=matter.id,
        received_on=date(2026, 8, 24),
        due_on=date(2026, 9, 8),
        items=[
            {"asked": ASKED[0], "ask": "document:poa"},
            {"asked": ASKED[1], "ask": "fact:gross_income"},
            {"asked": ASKED[2], "ask": "fact:resources", "as_of": date(2026, 8, 1)},
        ],
    )
    await db.commit()
    return matter.id, request.id


class TestARequest:
    @pytest.mark.asyncio
    async def test_one_letter_three_demands_one_deadline(
        self, async_db_session: AsyncSession
    ) -> None:
        """Which is why the obligation cannot be a field on the
        document: one document, many asks, resolved independently."""
        _matter, request_id = await _renewal(async_db_session)
        requests = RequestService(async_db_session)

        request = await requests.get(request_id)
        items = await requests.items(request_id)

        assert request.due_on == date(2026, 9, 8)
        assert [item.ordinal for item in items] == [1, 2, 3]
        # The sentence AS WRITTEN survives beside our normalisation of
        # it: a paraphrase that replaces the original loses the only
        # wording you are held to.
        assert items[1].asked == ASKED[1]
        assert items[1].ask == "fact:gross_income"
        assert items[2].as_of == date(2026, 8, 1)

    @pytest.mark.asyncio
    async def test_settling_one_item_leaves_the_others_standing(
        self, async_db_session: AsyncSession
    ) -> None:
        _matter, request_id = await _renewal(async_db_session)
        requests = RequestService(async_db_session)
        items = await requests.items(request_id)

        await requests.mark(items[0].id, "satisfied", "Filed 2026-08-28")
        await async_db_session.commit()

        assert standing(await requests.items(request_id)) == (1, 3)
        assert (await requests.get(request_id)).status == "open"

    @pytest.mark.asyncio
    async def test_a_request_is_satisfied_only_when_every_item_is(
        self, async_db_session: AsyncSession
    ) -> None:
        """Derived, never set: a request that says satisfied while an
        item says needed is a record arguing with itself."""
        _matter, request_id = await _renewal(async_db_session)
        requests = RequestService(async_db_session)

        for item in await requests.items(request_id):
            await requests.mark(item.id, "satisfied")
        await async_db_session.commit()

        assert (await requests.get(request_id)).status == "satisfied"

    @pytest.mark.asyncio
    async def test_an_item_can_close_without_being_answered(
        self, async_db_session: AsyncSession
    ) -> None:
        """ "Waived" is the agency's word and "not applicable" is yours.
        Both close an item that was never answered, and neither can be
        inferred - which is why both are recorded."""
        _matter, request_id = await _renewal(async_db_session)
        requests = RequestService(async_db_session)
        items = await requests.items(request_id)

        await requests.mark(items[0].id, "satisfied")
        await requests.mark(
            items[1].id, "waived", "County says the award letter covers it"
        )
        await requests.mark(items[2].id, "not_applicable")
        await async_db_session.commit()

        assert (await requests.get(request_id)).status == "satisfied"

    @pytest.mark.asyncio
    async def test_overdue_is_read_from_the_clock_not_a_column(
        self, async_db_session: AsyncSession
    ) -> None:
        """A flag written last night is a lie this morning, and the one
        thing a deadline must not do is go quiet."""
        _matter, request_id = await _renewal(async_db_session)
        requests = RequestService(async_db_session)
        request = await requests.get(request_id)

        assert overdue(request, date(2026, 9, 7)) is False
        assert overdue(request, date(2026, 9, 9)) is True

        for item in await requests.items(request_id):
            await requests.mark(item.id, "satisfied")
        await async_db_session.commit()

        # Answered is not late, however long it took.
        assert overdue(await requests.get(request_id), date(2026, 12, 1)) is False

    @pytest.mark.asyncio
    async def test_an_item_needs_the_sentence_that_was_asked(
        self, async_db_session: AsyncSession
    ) -> None:
        matter = await MatterService(async_db_session).open(title="Medicaid renewal")
        with pytest.raises(ValueError, match="sentence that was asked"):
            await RequestService(async_db_session).record(
                matter_id=matter.id, items=[{"ask": "document:poa"}]
            )


class TestTheDocumentThatAnswers:
    @pytest.mark.asyncio
    async def test_attaching_is_the_answer_and_taking_it_back_undoes_it(
        self, async_db_session: AsyncSession
    ) -> None:
        """Somebody who has found the power of attorney and put it here
        is not then asked to say separately that the item is done - and
        an item whose only evidence is taken away is not answered."""
        _matter, request_id = await _renewal(async_db_session)
        requests = RequestService(async_db_session)
        items = await requests.items(request_id)

        await requests.attach(items[0].id, 4242, "Power of attorney.pdf")
        await async_db_session.commit()

        answered = await requests.item(items[0].id)
        assert answered is not None
        assert answered.document_id == 4242
        assert answered.status == "satisfied"
        assert answered.resolution == "Power of attorney.pdf"

        await requests.detach(items[0].id)
        await async_db_session.commit()

        back = await requests.item(items[0].id)
        assert back is not None
        assert back.document_id is None
        assert back.status == "needed"
        assert standing(await requests.items(request_id)) == (0, 3)


class TestIllianaCanCorrectAnAsk:
    """The letter and the record disagree, and she has read the letter.
    Through a card, the way every write goes."""

    @pytest.mark.asyncio
    async def test_the_card_shows_before_and_after_and_the_change_lands(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import (
            AmendAskPayload,
            amend_ask_describe,
            amend_ask_execute,
        )

        _matter_id, request_id = await _renewal(async_db_session)
        item = (await RequestService(async_db_session).items(request_id))[1]
        payload = AmendAskPayload(
            item_id=item.id,
            asked="Proof of gross income as of 7/1/2026 for the NYSLRS pension",
            reason="The letter, page 1: 'proof of your NYSLRS pension'",
        )

        said = {
            r.label: r.value
            for r in await amend_ask_describe(async_db_session, payload, None)
        }
        assert said["Ask"] == ASKED[1]
        assert said["Would read"].endswith("NYSLRS pension")
        assert "page 1" in said["Because"]

        await amend_ask_execute(async_db_session, payload, None)
        await async_db_session.commit()
        assert (await RequestService(async_db_session).item(item.id)).asked.endswith(
            "NYSLRS pension"
        )

    @pytest.mark.asyncio
    async def test_an_ask_the_letter_makes_can_be_added(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import AddAskPayload, add_ask_execute

        _matter_id, request_id = await _renewal(async_db_session)
        await add_ask_execute(
            async_db_session,
            AddAskPayload(
                request_id=request_id, asked="Proof of all resources on 7/1/2026"
            ),
            None,
        )
        await async_db_session.commit()
        asked = [
            i.asked for i in await RequestService(async_db_session).items(request_id)
        ]
        assert asked == [*ASKED, "Proof of all resources on 7/1/2026"]

    def test_a_kind_nobody_defined_is_refused(self) -> None:
        from pydantic import ValidationError

        from app.services.matters.changes import AmendAskPayload

        with pytest.raises(ValidationError):
            AmendAskPayload(item_id=1, kind="wish")


class TestThePaperThatAnswersAnAsk:
    @pytest.mark.asyncio
    async def test_attaching_answers_the_ask_and_files_it_on_the_matter(
        self, async_db_session: AsyncSession
    ) -> None:
        """Illiana finds the statement on the shelf and puts it on the
        step: the item is satisfied by that document, and the document is
        on the matter's paper, the way the web's Attach does it."""
        from app.services.documents.service import DocumentService
        from app.services.matters.changes import (
            AttachAskPayload,
            attach_ask_describe,
            attach_ask_execute,
        )
        from app.services.matters.models import matter_tag
        from tests._pdf import pdf_bytes

        matter_id, request_id = await _renewal(async_db_session)
        item = (await RequestService(async_db_session).items(request_id))[1]
        documents = DocumentService(async_db_session)
        document = await documents.ingest(
            pdf_bytes(["Net Benefit: $1004.93"]),
            title="NYSLRS Monthly Statement.pdf",
            media_type="application/pdf",
            source="upload",
        )
        payload = AttachAskPayload(
            item_id=item.id,
            document_id=document.id,
            reason="The statement names the pension the ask is for",
        )

        said = {
            r.label: r.value
            for r in await attach_ask_describe(async_db_session, payload, None)
        }
        assert said["Ask"] == ASKED[1]
        assert said["Document"] == "NYSLRS Monthly Statement.pdf"
        assert "pension" in said["Because"]

        await attach_ask_execute(async_db_session, payload, None)
        await async_db_session.commit()
        answered = await RequestService(async_db_session).item(item.id)
        assert answered.document_id == document.id
        assert answered.status == "satisfied"
        assert matter_tag(matter_id) in await documents.tags_for(document.id)

    @pytest.mark.asyncio
    async def test_a_document_nobody_filed_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import AttachAskPayload, attach_ask_execute

        _matter_id, request_id = await _renewal(async_db_session)
        item = (await RequestService(async_db_session).items(request_id))[0]
        with pytest.raises(ValueError, match="document"):
            await attach_ask_execute(
                async_db_session,
                AttachAskPayload(item_id=item.id, document_id=999999),
                None,
            )
