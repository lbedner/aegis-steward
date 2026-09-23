"""Paper arrives named after a file, and a filename is not a name.

"20260826-statements-3639-.pdf" is what the bank called the download. It
tells a reader nothing, it cannot be asked for out loud, and it is why a
conversation about a document falls back to its id - which is a thing
nobody knows or should have to (2026-09-18).

No model. A title LOOKS like prose, which is what made reaching for one
tempting, but every part of it is already read: the kind and the date
come off the front page by pattern, and the organization comes from the
address book. Joining them is exact, free, and checkable by the person
approving it.
"""

from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.documents.domains.reading.titles import (
    compose,
    looks_like_a_filename,
    whose_letterhead,
)
from tests._session import opens

PAGES: list[Any] = [
    {
        "page": 1,
        "text": (
            "HUDSON VALLEY CREDIT UNION\n"
            "Statement period 08/01/2026 - 08/26/2026\n"
            "CLASSIC CHECKING ending 3639\n"
        ),
    },
    {"page": 2, "text": "Delta Dental of New York, Inc."},
]

ON_FILE = (
    "Hudson Valley Credit Union",
    "Delta Dental of New York, Inc.",
    "Hudson Valley",
)


class TestWhatCountsAsAName:
    def test_a_download_is_not_a_name(self) -> None:
        for filename in (
            "20260826-statements-3639-.pdf",
            "AP_DRTNY107_23548895152718_44328175.pdf",
            "scan0001.PDF",
            "IMG_4821.jpeg",
            "ppo_policy_document.pdf",
            "enrollee-notices-flyer.pdf",
        ):
            assert looks_like_a_filename(filename)

    def test_a_date_for_a_name_is_still_a_download(self) -> None:
        """ "September 07.pdf" has a space in it and is nobody's idea of a
        name: it is what the bank called the file. The stem rule read it
        as typed by a person and left the document unnamed on the real
        shelf (2026-09-19)."""
        for filename in (
            "September 07.pdf",
            "Aug 2026.pdf",
            "2026 08 17.pdf",
            "March 3 2026.PDF",
        ):
            assert looks_like_a_filename(filename), filename

    def test_something_somebody_typed_is_left_alone(self) -> None:
        """A title a person already gave it is not ours to improve - and
        the test is the STEM, so a good name carrying ".pdf" is a good
        name."""
        for named in (
            "HVCU statement, August 2026",
            "Medicaid renewal request",
            "Dad's POA",
            "NYSLRS Monthly Statement.pdf",
            "Executed POA for Bedner.pdf",
        ):
            assert not looks_like_a_filename(named)


class TestWhoseLetterheadItIs:
    def test_the_organization_on_the_front(self) -> None:
        found = whose_letterhead(PAGES, ON_FILE)
        assert found is not None
        assert found.value == "Hudson Valley Credit Union"
        assert found.page == 1
        # Cited: the line it was read off, so the card can show it.
        assert found.because == "HUDSON VALLEY CREDIT UNION"

    def test_the_longest_name_wins(self) -> None:
        """ "Hudson Valley" is also on file, and a shelf of documents from
        "Hudson Valley" is a shelf that lost the credit union."""
        assert whose_letterhead(PAGES, ON_FILE).value == "Hudson Valley Credit Union"

    def test_a_name_inside_a_longer_word_is_not_a_name(self) -> None:
        """Live: "Chase" was read off the line "Purchases +$0.00" and
        offered as a contact, twice. A brand directory of a hundred
        short names will find itself inside ordinary words all day
        (2026-09-19)."""
        for line, name in (
            ("Purchases +$0.00", "Chase"),
            ("PURCHASES", "Chase"),
            ("Citibank of somewhere", "Citi"),
        ):
            assert whose_letterhead([{"page": 1, "text": line}], (name,)) is None, line

    def test_a_name_at_a_word_boundary_is_one(self) -> None:
        for line, name in (
            ("Chase Bank", "Chase"),
            ("citi", "Citi"),
            ("American Express® Gold Card", "American Express"),
            ("Delta Dental of New York, Inc.", "Delta Dental of New York, Inc."),
        ):
            assert whose_letterhead([{"page": 1, "text": line}], (name,)), line

    def test_a_stranger_is_not_guessed_at(self) -> None:
        """Only organizations already on file. A letterhead read off the
        page and taken as a name is how "state.ny.us" became a website."""
        assert whose_letterhead(PAGES, ("Chase",)) is None

    def test_nobody_on_the_front_is_nobody(self) -> None:
        assert whose_letterhead([{"page": 1, "text": "Page 1 of 6"}], ON_FILE) is None


class TestTheNameItself:
    def test_who_what_and_when(self) -> None:
        found = compose(whose_letterhead(PAGES, ON_FILE), "statement", "2026-08-26")
        assert found.value == "Hudson Valley Credit Union statement, August 2026"

    def test_what_is_not_known_is_left_out(self) -> None:
        head = whose_letterhead(PAGES, ON_FILE)
        assert (
            compose(head, "statement", None).value
            == "Hudson Valley Credit Union statement"
        )
        assert (
            compose(head, None, "2026-08-26").value
            == "Hudson Valley Credit Union, August 2026"
        )

    def test_the_papers_own_heading_tells_two_of_a_kind_apart(self) -> None:
        """Two Delta Dental documents both came out "Delta Dental of New
        York, Inc. form", which separates them no better than their
        filenames did. The heading the kind was read FROM is the
        distinguishing thing, and it is already in hand."""
        from app.services.documents.domains.reading.findings import Finding

        head = whose_letterhead(PAGES, ON_FILE)
        application = Finding("kind", "form", 1, "Application")
        contract = Finding("kind", "form", 1, "Combined Contract and Disclosure Form")
        assert compose(head, application, None).value == (
            "Hudson Valley Credit Union Application"
        )
        assert compose(head, contract, None).value == (
            "Hudson Valley Credit Union Combined Contract and Disclosure Form"
        )

    def test_a_heading_that_already_names_the_sender_stands_alone(self) -> None:
        """ "Hudson Valley Credit Union Welcome to Hudson Valley Credit
        Union" says it twice."""
        from app.services.documents.domains.reading.findings import Finding

        welcome = Finding("kind", "letter", 1, "Welcome to Hudson Valley Credit Union")
        said = compose(whose_letterhead(PAGES, ON_FILE), welcome, None)
        assert said.value == "Welcome to Hudson Valley Credit Union"

    def test_an_identifier_is_not_part_of_a_name(self) -> None:
        """ "GreenSky Application ID: 1903014447" was proposed as a title.
        The ID is the one thing on that heading nobody would say out
        loud, and a name with a ten-digit number in it cannot be
        recognised in a list (2026-09-19)."""
        from app.services.documents.domains.reading.findings import Finding

        for heading, expected in (
            ("Application ID: 1903014447", "Hudson Valley Credit Union Application"),
            ("Statement #00123456", "Hudson Valley Credit Union Statement"),
            ("Account Summary", "Hudson Valley Credit Union Account Summary"),
        ):
            said = compose(
                whose_letterhead(PAGES, ON_FILE),
                Finding("kind", "form", 1, heading),
                None,
            )
            assert said.value == expected, heading

    def test_a_heading_that_is_really_a_sentence_falls_back_to_the_kind(
        self,
    ) -> None:
        from app.services.documents.domains.reading.findings import Finding

        wordy = Finding(
            "kind", "statement", 1, "Your statement of benefits for the plan year 2026"
        )
        said = compose(whose_letterhead(PAGES, ON_FILE), wordy, None)
        assert said.value == "Hudson Valley Credit Union statement"

    def test_an_organization_alone_is_not_a_name(self) -> None:
        """Run over a real shelf, this named four different documents
        "Delta Dental of New York, Inc.". Four papers with one name is
        the filename problem in tidier clothes, so the ones it cannot
        tell apart keep the filename and stay findable."""
        assert compose(whose_letterhead(PAGES, ON_FILE), None, None) is None
        assert compose(whose_letterhead(PAGES, ON_FILE), "other", None) is None

    def test_without_an_organization_there_is_no_name(self) -> None:
        """ "statement, August 2026" is a category, not a name - and a
        shelf of those is the filename problem in tidier clothes."""
        assert compose(None, "statement", "2026-08-26") is None

    def test_a_name_too_long_to_read_is_refused(self) -> None:
        from app.services.documents.domains.reading.findings import Finding

        long = Finding("sender", "x" * 90, 1, "x" * 90)
        assert compose(long, "statement", "2026-08-26") is None


class TestWhoSentIt:
    """Drop a document in and it should say who it is from, without
    being asked. The letterhead reader that names the paper already
    knows - it just never offered (2026-09-19)."""

    @pytest.mark.asyncio
    async def test_the_letterhead_is_proposed_as_the_sender(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.documents.models import Document, DocumentPage
        from app.services.matters.service import PartyService

        hvcu = await PartyService(async_db_session).create(
            name="Hudson Valley Credit Union", kind="organization"
        )
        document = Document(
            title="20260826-statements-3639-.pdf",
            storage_key="testcase/sender.pdf",
            media_type="application/pdf",
            content_hash="testcase-sender-1",
            size_bytes=9,
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                status="read",
                method="text",
                text=PAGES[0]["text"],
            )
        )
        await async_db_session.flush()

        change = await propose_reading(opens(async_db_session), document.id)

        # An ID, because that is what the payload takes - a name typed
        # there is a second directory nobody can join to the first.
        assert change.payload["sender"]["value"] == str(hvcu.id)
        assert change.payload["sender"]["because"] == "HUDSON VALLEY CREDIT UNION"

    @pytest.mark.asyncio
    async def test_a_stranger_is_not_proposed(
        self, async_db_session: AsyncSession
    ) -> None:
        """Only organizations already on file. Guessing a sender from a
        letterhead is how a second address book starts."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.documents.models import Document, DocumentPage

        document = Document(
            title="unknown-sender.pdf",
            storage_key="testcase/sender2.pdf",
            media_type="application/pdf",
            content_hash="testcase-sender-2",
            size_bytes=9,
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                status="read",
                method="text",
                text="SOME BANK NOBODY HAS MET\nStatement period 08/2026\n",
            )
        )
        await async_db_session.flush()

        change = await propose_reading(opens(async_db_session), document.id)
        assert change is None or change.payload.get("sender") is None


class TestWhichCaseItBelongsTo:
    """A letter that quotes a case number belongs to that case, and the
    app should say so when the paper lands rather than wait to be told.
    The county writes MA258760XX on every page of every letter."""

    async def _renewal(self, db: AsyncSession, text: str) -> Any:
        from app.services.documents.models import Document, DocumentPage
        from app.services.matters.matters import MatterService

        matter = await MatterService(db).open(
            title="Medicaid renewal", reference="MA258760XX"
        )
        document = Document(
            title="scan0004.pdf",
            storage_key=f"testcase/{abs(hash(text))}.pdf",
            media_type="application/pdf",
            content_hash=f"testcase-case-{abs(hash(text))}",
            size_bytes=9,
        )
        db.add(document)
        await db.flush()
        db.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                status="read",
                method="text",
                text=text,
            )
        )
        await db.flush()
        return matter, document

    @pytest.mark.asyncio
    async def test_a_quoted_case_number_files_the_paper(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading

        matter, document = await self._renewal(
            async_db_session,
            "DUTCHESS COUNTY DSS\nCase MA258760XX\nRenewal of eligibility\n",
        )

        change = await propose_reading(opens(async_db_session), document.id)
        assert change.payload["matter"]["value"] == str(matter.id)
        # Cited, like every other reading on this card.
        assert "MA258760XX" in change.payload["matter"]["because"]

    @pytest.mark.asyncio
    async def test_paper_that_names_no_case_is_filed_nowhere(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading

        _matter, document = await self._renewal(
            async_db_session, "DUTCHESS COUNTY DSS\nSomething else entirely\n"
        )
        change = await propose_reading(opens(async_db_session), document.id)
        assert change is None or change.payload.get("matter") is None

    @pytest.mark.asyncio
    async def test_approving_it_puts_the_paper_on_the_case(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            metadata_execute,
        )
        from app.services.matters.matters import MatterService

        matter, document = await self._renewal(async_db_session, "Case MA258760XX\n")
        await metadata_execute(
            async_db_session,
            MetadataPayload(
                document_id=document.id,
                matter={
                    "value": str(matter.id),
                    "page": 1,
                    "because": "Case MA258760XX",
                },
            ),
            None,
        )
        await async_db_session.flush()

        assert await MatterService(async_db_session).for_document(document.id) == (
            matter.id
        )


class TestALetterheadNobodyHasMetYet:
    """The app knows these brands - just not in the address book.

    Three cards came out of the real shelf naming nobody, because their
    letterheads say American Express, citi and GreenSky and none of them
    is a contact. All three are in the payee directory or the
    institution list, which the letterhead reader was not looking at
    (2026-09-19). So it looks in every drawer, and where the brand is
    known but not filed, it offers to file it.
    """

    async def _paper_from(self, db: AsyncSession, letterhead: str) -> Any:
        from app.services.documents.models import Document, DocumentPage

        document = Document(
            title="statement-0001.pdf",
            storage_key=f"testcase/{abs(hash(letterhead))}.pdf",
            media_type="application/pdf",
            content_hash=f"testcase-stranger-{abs(hash(letterhead))}",
            size_bytes=9,
        )
        db.add(document)
        await db.flush()
        db.add(
            DocumentPage(
                document_id=document.id,
                page_number=1,
                status="read",
                method="text",
                text=f"{letterhead}\nAccount Summary\nNew balance $45,787.53\n",
            )
        )
        await db.flush()
        return document

    @pytest.mark.asyncio
    async def test_a_payee_on_the_letterhead_is_offered_as_a_contact(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from tests.services._finance_factories import seed_merchant

        await seed_merchant(async_db_session, "American Express")
        document = await self._paper_from(async_db_session, "American Express")

        await propose_reading(opens(async_db_session), document.id)

        # The queue, not the return value: reading a document proposes
        # several cards and the metadata one is what it hands back.
        offered = await _contacts_offered(async_db_session)
        assert [one.payload["name"] for one in offered] == ["American Express"]
        assert offered[0].payload["kind"] == "organization"
        # Cited: the page and the line it was read off, like everything
        # else this surface proposes.
        assert "American Express" in offered[0].payload["note"]

    @pytest.mark.asyncio
    async def test_a_brand_mentioned_in_a_sentence_is_not_a_letterhead(
        self, async_db_session: AsyncSession
    ) -> None:
        """ "Target Date Fund 2045" is not a letter from Target, and a
        payee directory of a hundred brands will match somebody in the
        prose of any long document. A letterhead IS the line it is on."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from tests.services._finance_factories import seed_merchant

        await seed_merchant(async_db_session, "Target")
        document = await self._paper_from(
            async_db_session, "Your Target Date Fund 2045 allocation changed"
        )

        await propose_reading(opens(async_db_session), document.id)
        assert await _contacts_offered(async_db_session) == []

    @pytest.mark.asyncio
    async def test_a_sender_already_on_file_is_not_offered_under_another_name(
        self, async_db_session: AsyncSession
    ) -> None:
        """A Chase statement offered to create a contact called "Chase"
        while "JPMorgan Chase Bank, N.A." was already in the address
        book: the letterhead matched a payee and the contact at once,
        and the stranger path did not look (2026-09-19). One sender per
        piece of paper."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.matters.service import PartyService
        from tests.services._finance_factories import seed_merchant

        await seed_merchant(async_db_session, "Chase")
        await PartyService(async_db_session).create(
            name="JPMorgan Chase Bank, N.A.", kind="organization"
        )
        await async_db_session.flush()
        document = await self._paper_from(async_db_session, "JPMorgan Chase Bank, N.A.")

        await propose_reading(opens(async_db_session), document.id)
        assert await _contacts_offered(async_db_session) == []

    @pytest.mark.asyncio
    async def test_a_brand_nobody_knows_is_not_invented(
        self, async_db_session: AsyncSession
    ) -> None:
        """Only names the app already holds somewhere. A letterhead read
        off the page and taken as an organization is how a second
        address book starts."""
        from app.services.documents.domains.reading.proposals import propose_reading

        document = await self._paper_from(async_db_session, "SOME BANK NOBODY HAS MET")

        await propose_reading(opens(async_db_session), document.id)
        assert await _contacts_offered(async_db_session) == []

    @pytest.mark.asyncio
    async def test_a_contact_already_on_file_is_not_offered_again(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.matters.service import PartyService
        from tests.services._finance_factories import seed_merchant

        await seed_merchant(async_db_session, "American Express")
        await PartyService(async_db_session).create(
            name="American Express", kind="organization"
        )
        document = await self._paper_from(async_db_session, "American Express")

        await propose_reading(opens(async_db_session), document.id)
        assert await _contacts_offered(async_db_session) == []


async def _contacts_offered(db: AsyncSession) -> list[Any]:
    """The contact.create cards waiting in the queue."""
    from app.services.finance.domains.writes.queue import list_changes

    return [
        change
        for change in await list_changes(db, status="pending")
        if change.change_type == "contact.create"
    ]


async def _front_page(db: AsyncSession, *lines: str) -> Any:
    """A document whose one read page says ``lines``."""
    from app.services.documents.models import Document, DocumentPage

    text = "\n".join(lines)
    document = Document(
        title="statement-0002.pdf",
        storage_key=f"testcase/{abs(hash(text))}.pdf",
        media_type="application/pdf",
        content_hash=f"testcase-front-{abs(hash(text))}",
        size_bytes=9,
    )
    db.add(document)
    await db.flush()
    db.add(
        DocumentPage(
            document_id=document.id,
            page_number=1,
            status="read",
            method="text",
            text=text,
        )
    )
    await db.flush()
    return document


class TestWhatElseTheFrontPageSays:
    """A letterhead is not the only thing on a page the app knows. A
    statement prints the account it is for; a letter prints the sender's
    phone or website where its name never appears in words."""

    @pytest.mark.asyncio
    async def test_the_account_a_statement_is_for(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.finance.service import FinanceService
        from tests.services._finance_factories import seed_account

        account = await seed_account(FinanceService(async_db_session), name="Card")
        account.mask = "3639"
        async_db_session.add(account)
        await async_db_session.flush()
        document = await _front_page(
            async_db_session, "SEPTEMBER STATEMENT", "Account ending 3639"
        )

        change = await propose_reading(opens(async_db_session), document.id)
        assert change.payload["account"]["value"] == str(account.id)
        assert "3639" in change.payload["account"]["because"]

    @pytest.mark.asyncio
    async def test_approving_it_files_the_paper_on_the_account(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.changes import (
            MetadataPayload,
            metadata_execute,
        )
        from app.services.documents.service import DocumentService
        from app.services.finance.constants import account_tag
        from app.services.finance.service import FinanceService
        from tests.services._finance_factories import seed_account

        account = await seed_account(FinanceService(async_db_session), name="Card")
        document = await _front_page(async_db_session, "Account ending 3639")

        await metadata_execute(
            async_db_session,
            MetadataPayload(
                document_id=document.id,
                account={
                    "value": str(account.id),
                    "page": 1,
                    "because": "Account ending 3639",
                },
            ),
            None,
        )
        await async_db_session.flush()

        assert account_tag(account.id) in await DocumentService(
            async_db_session
        ).tags_for(document.id)

    @pytest.mark.asyncio
    async def test_a_sender_known_by_their_website_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        """The Delta Dental flyer's first line is deltadentalins.com and
        its name never appears as words a pattern can use."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Delta Dental of New York, Inc.",
            kind="organization",
            contact={"website": "https://www.deltadentalins.com"},
        )
        await async_db_session.flush()
        document = await _front_page(
            async_db_session, "deltadentalins.com", "Our PPO plans are underwritten"
        )

        change = await propose_reading(opens(async_db_session), document.id)
        assert change.payload["sender"]["value"] == str(party.id)
        # The whole line, not a substring of it: a citation says which
        # line was read, so an assertion that would pass on a longer one
        # is not checking the citation.
        assert change.payload["sender"]["because"] == "deltadentalins.com"

    @pytest.mark.asyncio
    async def test_a_sender_known_by_the_phone_they_print(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Dutchess County DSS",
            kind="organization",
            contact={"phone": "(845) 486-3000"},
        )
        await async_db_session.flush()
        document = await _front_page(
            async_db_session, "NOTICE OF RENEWAL", "Questions? Call 845-486-3000"
        )

        change = await propose_reading(opens(async_db_session), document.id)
        assert change.payload["sender"]["value"] == str(party.id)

    @pytest.mark.asyncio
    async def test_the_name_on_the_letterhead_still_wins(
        self, async_db_session: AsyncSession
    ) -> None:
        """A phone in a footer is weaker evidence than a name across the
        top, and two readings of one sender must not disagree."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.matters.service import PartyService

        named = await PartyService(async_db_session).create(
            name="Hudson Valley Credit Union", kind="organization"
        )
        await PartyService(async_db_session).create(
            name="Dutchess County DSS",
            kind="organization",
            contact={"phone": "(845) 486-3000"},
        )
        await async_db_session.flush()
        document = await _front_page(
            async_db_session,
            "HUDSON VALLEY CREDIT UNION",
            "Their county office: 845-486-3000",
        )

        change = await propose_reading(opens(async_db_session), document.id)
        assert change.payload["sender"]["value"] == str(named.id)


class TestWhoHoldsTheAccount:
    """Paper from the bank about an account says where the account is
    held. The Citizens 1098 was filed under Citizens and onto Citizens
    Bank Mortgage, and the account stayed held with nobody (#234)."""

    async def _held_nowhere(self, db: AsyncSession, mask: str) -> Any:
        from app.services.finance.service import FinanceService
        from tests.services._finance_factories import seed_account

        account = await seed_account(FinanceService(db), name=f"Loan {mask}")
        account.mask = mask
        db.add(account)
        await db.flush()
        return account

    async def _bank(self, db: AsyncSession, name: str) -> Any:
        from app.services.matters.service import PartyService

        return await PartyService(db).create(name=name, kind="organization")

    @pytest.mark.asyncio
    async def test_a_statement_from_the_bank_says_where_it_is_held(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading

        bank = await self._bank(async_db_session, "Testcase Savings")
        account = await self._held_nowhere(async_db_session, "4471")
        document = await _front_page(
            async_db_session,
            "Testcase Savings",
            "Account Statement",
            "Account ending 4471",
        )

        await propose_reading(opens(async_db_session), document.id)

        assert [
            (c.payload["account_id"], c.payload["party_id"])
            for c in await _banks_offered(async_db_session)
        ] == [(account.id, bank.id)]

    @pytest.mark.asyncio
    async def test_the_account_it_is_already_filed_on_counts(
        self, async_db_session: AsyncSession
    ) -> None:
        """The real 1098 prints no last four; it was filed on the account
        by hand."""
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.documents.service import DocumentService
        from app.services.finance.constants import account_tag

        bank = await self._bank(async_db_session, "Testcase Mortgage Co")
        account = await self._held_nowhere(async_db_session, "9902")
        document = await _front_page(
            async_db_session,
            "Testcase Mortgage Co",
            "2025 MORTGAGE INTEREST RECEIVED FROM PAYER/BORROWER(S) $7,568.77",
        )
        await DocumentService(async_db_session).tag(
            document.id, account_tag(account.id)
        )

        await propose_reading(opens(async_db_session), document.id)

        assert [
            (c.payload["account_id"], c.payload["party_id"])
            for c in await _banks_offered(async_db_session)
        ] == [(account.id, bank.id)]

    @pytest.mark.asyncio
    async def test_a_bank_already_set_is_never_moved(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading
        from app.services.finance.domains.ledger.accounts import (
            get_or_create_institution,
        )

        await self._bank(async_db_session, "Testcase Trust")
        account = await self._held_nowhere(async_db_session, "5510")
        elsewhere = await get_or_create_institution(async_db_session, name="Elsewhere")
        account.institution_id = elsewhere.id
        async_db_session.add(account)
        await async_db_session.flush()
        document = await _front_page(
            async_db_session,
            "Testcase Trust",
            "Account Statement",
            "Account ending 5510",
        )

        await propose_reading(opens(async_db_session), document.id)

        assert await _banks_offered(async_db_session) == []

    @pytest.mark.asyncio
    async def test_a_letter_quoting_the_account_is_not_the_bank(
        self, async_db_session: AsyncSession
    ) -> None:
        """The county quotes your account number when it asks for its
        statements; that does not make the county your bank."""
        from app.services.documents.domains.reading.proposals import propose_reading

        await self._bank(async_db_session, "Testcase County DSS")
        await self._held_nowhere(async_db_session, "6620")
        document = await _front_page(
            async_db_session,
            "Testcase County DSS",
            "Dear Mr. Bedner:",
            "Send statements for account ending 6620.",
        )

        await propose_reading(opens(async_db_session), document.id)

        assert await _banks_offered(async_db_session) == []

    @pytest.mark.asyncio
    async def test_reading_again_does_not_stack_a_second_card(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.domains.reading.proposals import propose_reading

        await self._bank(async_db_session, "Testcase Federal")
        await self._held_nowhere(async_db_session, "7731")
        document = await _front_page(
            async_db_session,
            "Testcase Federal",
            "Account Statement",
            "Account ending 7731",
        )

        await propose_reading(opens(async_db_session), document.id)
        await propose_reading(opens(async_db_session), document.id)

        assert len(await _banks_offered(async_db_session)) == 1


async def _banks_offered(db: AsyncSession) -> list[Any]:
    """The account.institution cards waiting in the queue."""
    from app.services.finance.domains.writes.queue import list_changes

    return [
        change
        for change in await list_changes(db, status="pending")
        if change.change_type == "account.institution"
    ]
