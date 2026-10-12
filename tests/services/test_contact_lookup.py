"""Reading the reach fields off paper already on the shelf.

The address, the phone and the website were printed on the letterhead
before anybody asked for them. Example County DSS puts its own street
address and switchboard at the top of every letter it sends, and the
contact record for it held nothing but an address somebody typed.

Conservative on purpose: patterns, never a model, and a page that
yields nothing yields nothing. A guessed phone number is worse than a
blank field, because a blank field is visibly missing and a wrong one
is not.
"""

from pathlib import Path

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

LETTERHEAD = """EXAMPLE COUNTY
DEPARTMENT OF
COMMUNITY AND FAMILY SERVICES
10 CENTER STREET
ANYTOWN, NY 12345
(555) 555-0161

James Doe Date : 8/27/2026
c/o Maple Nursing Care Center
100 N. Orchard Lane Case #; MA123456XX
Lakeside, NY 12346

REQUEST FOR INFORMATION / REQUEST TO TAKE ACTION
In order to determine your eligibility for assistance, we need either
the information listed below or you to take the action listed below.
Questions: call your examiner at 555-555-0163 or fax 555-555-0164.
Visit us at www.example.gov/dcfs for more.
"""


class TestWhatAPageOffers:
    """The extractor alone, with no database in it."""

    def test_it_finds_the_letterhead_block(self) -> None:
        from app.services.matters.lookup import found_in

        found = found_in(LETTERHEAD)
        assert found["phone"] == "(555) 555-0161"
        assert found["website"] == "www.example.gov/dcfs"
        assert "10 Center Street" in found["address"]
        assert "Anytown, NY 12345" in found["address"]

    def test_the_first_phone_wins_because_letterheads_lead(self) -> None:
        """Three numbers on the page and only one is the switchboard.
        The others are labelled lines, and a label this cannot read is a
        line it must not invent - so they are offered separately, never
        promoted into ``phone``."""
        from app.services.matters.lookup import found_in

        found = found_in(LETTERHEAD)
        assert found["phone"] == "(555) 555-0161"
        assert "555-555-0163" not in found.values()

    def test_a_box_is_an_address_too(self) -> None:
        """What large organizations actually print. Delta Dental and
        Chase both use one, and a rule that only knows house numbers
        reads neither of their addresses."""
        from app.services.matters.lookup import found_in

        found = found_in("Delta Dental\nP.O. Box 660138\nDallas, TX 75266-0138")
        assert found["address"] == "P.O. Box 660138, Dallas, TX 75266-0138"

    def test_a_page_with_nothing_offers_nothing(self) -> None:
        from app.services.matters.lookup import found_in

        assert found_in("Net Benefit: $1004.93\nThis is a lifetime benefit.") == {}

    def test_a_case_number_is_not_a_phone_number(self) -> None:
        """``MA123456XX`` and ``12345678901234`` are on this paper too.
        A digit run is not a phone number."""
        from app.services.matters.lookup import found_in

        found = found_in("Case #; MA123456XX ref 12345678901234 acct 1234509876")
        assert "phone" not in found

    def test_an_email_the_letterhead_vouches_for_is_read(self) -> None:
        from app.services.matters.lookup import found_in

        found = found_in("Visit www.example.gov/dcfs or write to info@example.gov.")
        assert found["email"] == "info@example.gov"

    def test_a_recipients_email_is_not_read_as_the_senders(self) -> None:
        """Found on the real shelf. A Delta Dental notice carried the
        enrollee's personal gmail, and reading it as Delta Dental's would
        have filed a household member's private address under an insurer.
        The domain has to match the site on the same page."""
        from app.services.matters.lookup import found_in

        found = found_in(
            "Delta Dental of New York\ndeltadentalins.com/about/contact/\n"
            "Enrollee: Alex B <someone@gmail.com>"
        )
        assert found["website"] == "deltadentalins.com/about/contact/"
        assert "email" not in found

    def test_a_lookalike_domain_does_not_vouch_for_an_email(self) -> None:
        """A suffix test on a hostname accepts the lookalike it exists to
        refuse: "notdeltadentalins.com" ends with "deltadentalins.com".
        The registrable domains have to be EQUAL."""
        from app.services.matters.lookup import found_in

        found = found_in(
            "notdeltadentalins.com and write to billing@deltadentalins.com"
        )
        assert found["website"] == "notdeltadentalins.com"
        assert "email" not in found

    def test_a_domain_inside_an_email_is_not_a_website(self) -> None:
        """Live, on the DSS letter: `Examiner@dfa.state.ny.us` was read as
        a website of `state.ny.us`, which then "corroborated" the email
        it had been extracted from. Two wrong fields propping each other
        up. An email is removed before any website is looked for."""
        from app.services.matters.lookup import found_in

        found = found_in("Contact P. Examiner at Examiner@dfa.state.ny.us today.")
        assert "website" not in found
        assert "email" not in found

    def test_a_real_site_beside_an_email_still_reads(self) -> None:
        from app.services.matters.lookup import found_in

        found = found_in("See www.example.gov/dcfs or mail Examiner@example.gov.")
        assert found["website"] == "www.example.gov/dcfs"
        assert found["email"] == "Examiner@example.gov"

    def test_an_email_with_nothing_to_vouch_for_it_is_dropped(self) -> None:
        """No letterhead site on the page means nothing corroborates the
        address, and a wrong email is worse than a blank one."""
        from app.services.matters.lookup import found_in

        assert "email" not in found_in("Reply to someone@gmail.com please.")


class TestTheLabelledLines:
    """Everything else the paper prints, with the word that names it.

    The DSS letter carries a fax, an examiner's direct line and a
    caseworker's email, all labelled, and the first pass read none of
    them: only the letterhead's first match per field. A number nobody
    labelled is a number whose meaning is lost, so the LABEL is what
    makes these worth keeping.

    The value is taken verbatim, the whole of the line after the colon.
    A person reads these; nothing parses them. "Jordan. Examiner@..."
    carries an OCR artifact, and showing it as printed is honest where
    quietly closing the gap would be a guess dressed as a reading.
    """

    def test_it_reads_a_labelled_number(self) -> None:
        from app.services.matters.lookup import lines_in

        found = lines_in("SSPA-45 fax: 555-555-0163")
        assert found == [("fax", "555-555-0163")]

    def test_it_keeps_the_value_as_printed(self) -> None:
        from app.services.matters.lookup import lines_in

        found = lines_in("Rev. 12/13 Email: Jordan. Examiner@dfa.state.ny.us")
        assert found == [("Email", "Jordan. Examiner@dfa.state.ny.us")]

    def test_a_line_with_no_way_to_reach_anyone_is_not_a_line(self) -> None:
        from app.services.matters.lookup import lines_in

        assert lines_in("Case #; MA123456XX") == []
        assert lines_in("Date : 8/27/2026") == []

    def test_an_unlabelled_number_is_dropped(self) -> None:
        """A label this cannot read is a line it must not invent."""
        from app.services.matters.lookup import lines_in

        assert lines_in("Call 555-555-0164 between 9 and 5.") == []

    def test_the_letterhead_number_is_not_repeated_as_a_line(self) -> None:
        from app.services.matters.lookup import found_in, lines_in

        page = "EXAMPLE COUNTY\n(555) 555-0161\nSSPA-45 fax: 555-555-0163"
        assert found_in(page)["phone"] == "(555) 555-0161"
        assert lines_in(page, besides={"(555) 555-0161"}) == [("fax", "555-555-0163")]


class TestWhatToProposeForAParty:
    @pytest.mark.asyncio
    async def test_it_offers_only_what_the_contact_lacks(
        self, async_db_session: AsyncSession
    ) -> None:
        """A field already on the record is not re-proposed. The card
        that changes nothing is the card nobody reads."""
        from app.services.documents.models import Document, DocumentTag
        from app.services.matters.lookup import contact_details
        from app.services.matters.models import party_tag
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Example Testcase",
            kind="organization",
            contact={"address": "10 Center Street, Anytown, NY 12345"},
        )
        document = Document(
            title="Request.pdf", kind="letter", storage_key="k1", content_hash="h1"
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentTag(document_id=document.id, label=party_tag(party.id))
        )
        await async_db_session.flush()
        await _page(async_db_session, document.id, 1, LETTERHEAD)

        offers = await contact_details(async_db_session, party.id)
        fields = {offer["field"]: offer for offer in offers}
        assert "address" not in fields, "already on the record"
        assert fields["phone"]["value"] == "(555) 555-0161"
        # Every offer says where it was read, or it is not an offer.
        assert fields["phone"]["document_id"] == document.id
        assert fields["phone"]["page"] == 1

    @pytest.mark.asyncio
    async def test_the_labelled_lines_come_through_with_their_labels(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.documents.models import Document, DocumentTag
        from app.services.matters.lookup import contact_details
        from app.services.matters.models import party_tag
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Lines Testcase",
            kind="organization",
            contact={"also": [{"label": "fax", "value": "555-555-0163"}]},
        )
        document = Document(
            title="Letter.pdf", kind="letter", storage_key="k3", content_hash="h3"
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentTag(document_id=document.id, label=party_tag(party.id))
        )
        await async_db_session.flush()
        await _page(
            async_db_session,
            document.id,
            1,
            "EXAMPLE COUNTY\n10 CENTER STREET\nANYTOWN, NY 12345\n"
            "(555) 555-0161\nSSPA-45 fax: 555-555-0163\nphone: 555-555-0164",
        )

        offers = await contact_details(async_db_session, party.id)
        lines = [o for o in offers if o["field"] == "also"]
        # The fax is already a line on the record, so only the examiner's
        # number is new - and the letterhead's own number is a main
        # field, not a line.
        assert [(o["label"], o["value"]) for o in lines] == [("phone", "555-555-0164")]
        assert lines[0]["document_id"] == document.id
        assert lines[0]["page"] == 1

    @pytest.mark.asyncio
    async def test_a_party_with_no_paper_offers_nothing(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.lookup import contact_details
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Paperless Testcase", kind="organization"
        )
        assert await contact_details(async_db_session, party.id) == []

    @pytest.mark.asyncio
    async def test_only_the_opening_pages_are_read(
        self, async_db_session: AsyncSession
    ) -> None:
        """Letterhead and footer carry the contact block. A phone number
        buried on page nine of a policy belongs to whatever that page is
        about, not to the sender."""
        from app.services.documents.models import Document, DocumentTag
        from app.services.matters.lookup import contact_details
        from app.services.matters.models import party_tag
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Deep Testcase", kind="organization"
        )
        document = Document(
            title="Policy.pdf", kind="letter", storage_key="k2", content_hash="h2"
        )
        async_db_session.add(document)
        await async_db_session.flush()
        async_db_session.add(
            DocumentTag(document_id=document.id, label=party_tag(party.id))
        )
        await async_db_session.flush()
        await _page(async_db_session, document.id, 1, "Cover page. Nothing here.")
        await _page(async_db_session, document.id, 9, "Call 800-555-1212 to appeal.")

        assert await contact_details(async_db_session, party.id) == []


async def _page(db: AsyncSession, document_id: int, number: int, text: str) -> None:
    from app.services.documents.models import DocumentPage

    db.add(
        DocumentPage(
            document_id=document_id,
            page_number=number,
            status="read",
            method="text",
            text=text,
        )
    )
    await db.flush()


def test_the_module_is_not_a_second_home_for_the_reach_shape() -> None:
    """``CONTACT_FIELDS`` decides what a contact can hold. A lookup that
    invents its own list is how the form and the reader drift."""
    source = Path("app/services/matters/lookup.py").read_text()
    assert "CONTACT_FIELDS" in source
