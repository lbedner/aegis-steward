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


class TestANewContact:
    @pytest.mark.asyncio
    async def test_a_person_is_created_with_how_to_reach_them(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            CreateContactPayload,
            create_contact_describe,
            create_contact_execute,
        )
        from app.services.matters.service import PartyService

        payload = CreateContactPayload(
            name="Spouse Testcase",
            address="3 Somewhere Cir, Poughkeepsie, NY 12601",
            note="Wife of the subject",
        )
        said = {
            r.label: r.value
            for r in await create_contact_describe(async_db_session, payload, None)
        }
        assert said["Contact"] == "Spouse Testcase"
        assert said["Address"].startswith("3 Somewhere")
        assert "Phone" not in said

        made = await create_contact_execute(async_db_session, payload, None)
        await async_db_session.commit()
        party = await PartyService(async_db_session).get(made["party_id"])
        assert party.kind == "person"
        assert party.contact == {"address": "3 Somewhere Cir, Poughkeepsie, NY 12601"}
        assert party.note == "Wife of the subject"

    def test_a_kind_nobody_defined_is_refused(self) -> None:
        from pydantic import ValidationError

        from app.services.matters.contacts import CreateContactPayload

        with pytest.raises(ValidationError):
            CreateContactPayload(name="X", kind="agency")


@pytest.mark.asyncio
async def test_an_item_keeps_the_kind_the_letter_made_it(
    async_db_session: AsyncSession,
) -> None:
    """``record`` took a kind and threw it away, so every ask a letter
    filed came out a "document" - including the ones that were figures
    and the ones that were forms. ``add_item`` had honoured it all
    along, which is how the two drifted apart unnoticed."""
    matter = await MatterService(async_db_session).open(title="Kinds", reference="K-1")
    request = await RequestService(async_db_session).record(
        matter_id=matter.id,
        items=[
            {"asked": "Proof of gross income", "kind": "figure"},
            {"asked": "Sign the enclosed form", "kind": "form"},
            {"asked": "A copy of the deed"},
        ],
    )

    items = await RequestService(async_db_session).items(request.id)
    assert [i.kind for i in items] == ["figure", "form", "document"]


class TestAmendingAContact:
    """Illiana can correct a contact she did not create.

    ``contact.create`` existed and nothing amended one, so a phone
    number learned about an existing person had nowhere to go. She said
    she had "saved" two numbers; nothing was written, because there was
    no card that could write them.
    """

    @pytest.mark.asyncio
    async def test_only_what_is_sent_changes(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_execute,
        )
        from app.services.matters.service import PartyService

        parties = PartyService(async_db_session)
        party = await parties.create(
            name="Leonard Testcase",
            kind="person",
            contact={"email": "leonard@example.com"},
            note="Head of household",
        )
        await amend_contact_execute(
            async_db_session,
            AmendContactPayload(
                party_id=party.id,
                address="3 Somewhere Cir, Poughkeepsie, NY 12601",
                phone="845-430-1070",
            ),
            None,
        )
        await async_db_session.commit()

        again = await parties.get(party.id)
        assert again.contact == {
            "email": "leonard@example.com",
            "address": "3 Somewhere Cir, Poughkeepsie, NY 12601",
            "phone": "845-430-1070",
        }
        # Untouched because unsent: a card that fills in a phone number
        # must not empty the note it said nothing about.
        assert again.note == "Head of household"
        assert again.name == "Leonard Testcase"

    @pytest.mark.asyncio
    async def test_the_card_shows_before_and_after_with_its_source(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_describe,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Delta Testcase",
            kind="organization",
            contact={"phone": "800-000-0000"},
        )
        said = {
            row.label: row.value
            for row in await amend_contact_describe(
                async_db_session,
                AmendContactPayload(
                    party_id=party.id,
                    phone="800-471-7091",
                    website="https://deltadental.example",
                    sources={"phone": "Document 12, page 1"},
                ),
                None,
            )
        }
        assert said["Contact"] == "Delta Testcase"
        # What it WAS is on the card: approving a correction blind is
        # how a good number gets overwritten by a worse one.
        assert said["Phone"] == "800-000-0000 → 800-471-7091 · Document 12, page 1"
        assert said["Website"] == "- → https://deltadental.example"

    @pytest.mark.asyncio
    async def test_a_card_that_would_change_nothing_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_describe,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Nothing Testcase", kind="person", contact={"phone": "845-616-9084"}
        )
        with pytest.raises(ValueError, match="nothing"):
            await amend_contact_describe(
                async_db_session,
                AmendContactPayload(party_id=party.id, phone="845-616-9084"),
                None,
            )

    @pytest.mark.asyncio
    async def test_a_contact_that_is_not_there_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_execute,
        )

        with pytest.raises(ValueError, match="999999"):
            await amend_contact_execute(
                async_db_session,
                AmendContactPayload(party_id=999999, phone="845-430-1070"),
                None,
            )

    def test_an_empty_card_is_refused_at_the_payload(self) -> None:
        from pydantic import ValidationError

        from app.services.matters.contacts import AmendContactPayload

        with pytest.raises(ValidationError):
            AmendContactPayload(party_id=1)

    @pytest.mark.asyncio
    async def test_the_labelled_lines_replace_rather_than_append(
        self, async_db_session: AsyncSession
    ) -> None:
        """Sending ``also`` replaces the whole list, and the card says
        so by drawing what was there beside what will be."""
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_describe,
            amend_contact_execute,
        )
        from app.services.matters.service import PartyService

        parties = PartyService(async_db_session)
        party = await parties.create(
            name="Chase Testcase",
            kind="organization",
            contact={"also": [{"label": "Claims fax", "value": "800-000-0001"}]},
        )
        payload = AmendContactPayload(
            party_id=party.id,
            also=[{"label": "Spanish line", "value": "800-000-0002"}],
        )
        said = {
            row.label: row.value
            for row in await amend_contact_describe(async_db_session, payload, None)
        }
        assert said["Claims fax"] == "800-000-0001 → -"
        assert said["Spanish line"] == "- → 800-000-0002"

        await amend_contact_execute(async_db_session, payload, None)
        await async_db_session.commit()
        again = await parties.get(party.id)
        assert again.contact["also"] == [
            {"label": "Spanish line", "value": "800-000-0002"}
        ]

    @pytest.mark.asyncio
    async def test_the_note_can_be_emptied_out(
        self, async_db_session: AsyncSession
    ) -> None:
        """The case the ticket was written for: Delta Dental's numbers
        went into the note because nothing could file them properly, and
        moving them out means the note has to be clearable."""
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_execute,
        )
        from app.services.matters.service import PartyService

        parties = PartyService(async_db_session)
        party = await parties.create(
            name="Noted Testcase",
            kind="organization",
            note="Phone 800-471-7091, deltadental.example",
        )
        await amend_contact_execute(
            async_db_session,
            AmendContactPayload(
                party_id=party.id,
                phone="800-471-7091",
                website="https://deltadental.example",
                note="",
            ),
            None,
        )
        await async_db_session.commit()
        again = await parties.get(party.id)
        assert again.note is None
        assert again.contact["phone"] == "800-471-7091"


class TestClearingAFieldThatShouldNotBeThere:
    """A reach field can be emptied, not only corrected.

    Live: a bad reading put a website of "state.ny.us" and a truncated
    caseworker email onto Dutchess County DSS. Nothing could take them
    off again - an unset field meant "say nothing about this", so there
    was no way to say "this should be blank" (2026-09-18). A record you
    can fill and cannot empty accumulates every mistake ever made in it.
    """

    @pytest.mark.asyncio
    async def test_an_empty_string_clears_a_reach_field(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_execute,
        )
        from app.services.matters.service import PartyService

        parties = PartyService(async_db_session)
        party = await parties.create(
            name="Wrong Testcase",
            kind="organization",
            contact={
                "address": "60 Market St",
                "phone": "(845) 486-3000",
                "website": "state.ny.us",
            },
        )
        await amend_contact_execute(
            async_db_session,
            AmendContactPayload(party_id=party.id, website=""),
            None,
        )
        await async_db_session.commit()

        again = await parties.get(party.id)
        assert "website" not in again.contact
        # Untouched, because unsent still means unsaid.
        assert again.contact["phone"] == "(845) 486-3000"
        assert again.contact["address"] == "60 Market St"

    @pytest.mark.asyncio
    async def test_the_card_says_it_is_being_emptied(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_describe,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Clear Testcase",
            kind="organization",
            contact={"website": "state.ny.us"},
        )
        said = {
            row.label: row.value
            for row in await amend_contact_describe(
                async_db_session,
                AmendContactPayload(party_id=party.id, website=""),
                None,
            )
        }
        assert said["Website"] == "state.ny.us → -"

    @pytest.mark.asyncio
    async def test_clearing_something_already_blank_changes_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.contacts import (
            AmendContactPayload,
            amend_contact_describe,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Blank Testcase", kind="organization", contact={"phone": "1"}
        )
        with pytest.raises(ValueError, match="nothing"):
            await amend_contact_describe(
                async_db_session,
                AmendContactPayload(party_id=party.id, website=""),
                None,
            )
