"""Reading the reach fields off paper already on the shelf.

The address, the phone and the website were printed on the letterhead
before anybody asked for them. Dutchess County DSS puts its own street
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

LETTERHEAD = """DUTCHESS COUNTY
DEPARTMENT OF
COMMUNITY AND FAMILY SERVICES
60 MARKET STREET
POUGHKEEPSIE, NY 12601
(845) 486-3000

James Bedner Date : 8/27/2026
c/o Eleanor Nursing Care Center
419 N. Quaker Lane Case #; MA258760XX
Hyde Park, NY 12538

REQUEST FOR INFORMATION / REQUEST TO TAKE ACTION
In order to determine your eligibility for assistance, we need either
the information listed below or you to take the action listed below.
Questions: call your examiner at 845-486-3301 or fax 845-486-3345.
Visit us at www.dutchessny.gov/dcfs for more.
"""


class TestWhatAPageOffers:
    """The extractor alone, with no database in it."""

    def test_it_finds_the_letterhead_block(self) -> None:
        from app.services.matters.lookup import found_in

        found = found_in(LETTERHEAD)
        assert found["phone"] == "(845) 486-3000"
        assert found["website"] == "www.dutchessny.gov/dcfs"
        assert "60 Market Street" in found["address"]
        assert "Poughkeepsie, NY 12601" in found["address"]

    def test_the_first_phone_wins_because_letterheads_lead(self) -> None:
        """Three numbers on the page and only one is the switchboard.
        The others are labelled lines, and a label this cannot read is a
        line it must not invent - so they are offered separately, never
        promoted into ``phone``."""
        from app.services.matters.lookup import found_in

        found = found_in(LETTERHEAD)
        assert found["phone"] == "(845) 486-3000"
        assert "845-486-3301" not in found.values()

    def test_a_page_with_nothing_offers_nothing(self) -> None:
        from app.services.matters.lookup import found_in

        assert found_in("Net Benefit: $1004.93\nThis is a lifetime benefit.") == {}

    def test_a_case_number_is_not_a_phone_number(self) -> None:
        """``MA258760XX`` and ``23548895152718`` are on this paper too.
        A digit run is not a phone number."""
        from app.services.matters.lookup import found_in

        found = found_in("Case #; MA258760XX ref 23548895152718 acct 4483109900")
        assert "phone" not in found

    def test_an_email_the_letterhead_vouches_for_is_read(self) -> None:
        from app.services.matters.lookup import found_in

        found = found_in(
            "Visit www.dutchessny.gov/dcfs or write to info@dutchessny.gov."
        )
        assert found["email"] == "info@dutchessny.gov"

    def test_a_recipients_email_is_not_read_as_the_senders(self) -> None:
        """Found on the real shelf. A Delta Dental notice carried the
        enrollee's personal gmail, and reading it as Delta Dental's would
        have filed a household member's private address under an insurer.
        The domain has to match the site on the same page."""
        from app.services.matters.lookup import found_in

        found = found_in(
            "Delta Dental of New York\ndeltadentalins.com/about/contact/\n"
            "Enrollee: Marisa B <someone@gmail.com>"
        )
        assert found["website"].startswith("deltadentalins.com")
        assert "email" not in found

    def test_a_domain_inside_an_email_is_not_a_website(self) -> None:
        """Live, on the DSS letter: `Traver@dfa.state.ny.us` was read as
        a website of `state.ny.us`, which then "corroborated" the email
        it had been extracted from. Two wrong fields propping each other
        up. An email is removed before any website is looked for."""
        from app.services.matters.lookup import found_in

        found = found_in("Contact P. Traver at Traver@dfa.state.ny.us today.")
        assert "website" not in found
        assert "email" not in found

    def test_a_real_site_beside_an_email_still_reads(self) -> None:
        from app.services.matters.lookup import found_in

        found = found_in(
            "See www.dutchessny.gov/dcfs or mail Traver@dutchessny.gov."
        )
        assert found["website"] == "www.dutchessny.gov/dcfs"
        assert found["email"] == "Traver@dutchessny.gov"

    def test_an_email_with_nothing_to_vouch_for_it_is_dropped(self) -> None:
        """No letterhead site on the page means nothing corroborates the
        address, and a wrong email is worse than a blank one."""
        from app.services.matters.lookup import found_in

        assert "email" not in found_in("Reply to someone@gmail.com please.")


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
            name="Dutchess Testcase",
            kind="organization",
            contact={"address": "60 Market Street, Poughkeepsie, NY 12601"},
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
        assert fields["phone"]["value"] == "(845) 486-3000"
        # Every offer says where it was read, or it is not an offer.
        assert fields["phone"]["document_id"] == document.id
        assert fields["phone"]["page"] == 1

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
