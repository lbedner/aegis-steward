"""What a document says about itself that is not its name.

A letterhead is not the only thing on the front of a page that the app
already knows. A statement prints the account it is for, a letter prints
the sender's phone and website, a bank prints its routing number - and
every one of those is a string this household already stores somewhere
(2026-09-19).

Same shape as the letterhead: a value on file, matched against the front
of the page, citing the line it was read from. Nothing is invented and
nothing is guessed from a name.
"""

import pytest

from app.services.documents.domains.reading.identity import Known, identify


def _pages(*lines: str) -> list[dict]:
    return [{"page": 1, "text": "\n".join(lines)}]


ACCOUNT = Known(kind="account", target=47, how="mask", value="3639")
DENTAL = Known(kind="party", target=7, how="domain", value="deltadentalins.com")
DSS = Known(kind="party", target=2, how="phone", value="(845) 486-3000")
BANK = Known(kind="institution", target=1, how="routing", value="221979363")


class TestTheAccountAStatementIsFor:
    def test_the_last_four_printed_the_way_a_statement_prints_them(self) -> None:
        for line in (
            "Account number ending 3639",
            "Account ending in 3639",
            "Card Account: ****3639",
            "ACCT #XXXX3639",
        ):
            found = identify(_pages(line), [ACCOUNT])
            assert [one.target for one, _ in found] == [47], line

    def test_the_whole_number_printed_bare(self) -> None:
        """A real Chase statement prints "000000957483639" on every page
        with no label at all. Fifteen digits ending in the last four on
        file is not a year, an amount or a coincidence (2026-09-19)."""
        found = identify(_pages("000000957483639"), [ACCOUNT])
        assert [one.target for one, _ in found] == [47]

    def test_a_long_number_that_ends_somewhere_else(self) -> None:
        assert identify(_pages("000000957481164"), [ACCOUNT]) == []

    def test_four_digits_alone_are_not_an_account(self) -> None:
        """A statement is full of four-digit numbers - a year, an amount,
        a phone's tail. Only the ones printed AS an account number
        count, or every document is about every account."""
        for line in (
            "New balance $3,639.00",
            "Statement period 08/01/2026 - 3639",
            "3639",
        ):
            assert identify(_pages(line), [ACCOUNT]) == []

    def test_a_different_account_is_not_this_one(self) -> None:
        assert identify(_pages("Account ending 1164"), [ACCOUNT]) == []


class TestTheSenderByWhatTheyPrint:
    def test_a_website_on_the_letterhead(self) -> None:
        found = identify(_pages("deltadentalins.com"), [DENTAL])
        assert [one.target for one, _ in found] == [7]

    def test_a_website_however_it_is_written(self) -> None:
        for line in ("www.deltadentalins.com", "https://deltadentalins.com/members"):
            assert identify(_pages(line), [DENTAL]), line

    def test_a_phone_however_it_is_punctuated(self) -> None:
        for line in (
            "Call us at (845) 486-3000",
            "845.486.3000",
            "1-845-486-3000",
        ):
            found = identify(_pages(line), [DSS])
            assert [one.target for one, _ in found] == [2], line

    def test_a_number_that_is_not_theirs(self) -> None:
        assert identify(_pages("Call us at (845) 486-3001"), [DSS]) == []

    def test_a_routing_number_names_the_bank(self) -> None:
        found = identify(_pages("Routing #221979363"), [BANK])
        assert [one.target for one, _ in found] == [1]

    def test_nine_digits_that_are_not_a_routing_number(self) -> None:
        assert identify(_pages("Member since 221979364"), [BANK]) == []


class TestWhatItCites:
    def test_every_hit_names_the_page_and_the_line(self) -> None:
        found = identify(
            [
                {"page": 1, "text": "DELTA DENTAL\nwww.deltadentalins.com"},
                {"page": 2, "text": "Account ending 3639"},
            ],
            [DENTAL, ACCOUNT],
        )
        cited = {one.how: (finding.page, finding.because) for one, finding in found}
        assert cited["domain"] == (1, "www.deltadentalins.com")
        assert cited["mask"] == (2, "Account ending 3639")

    def test_only_the_front_of_the_document(self) -> None:
        """Page nine of a policy naming an insurer is boilerplate - the
        same reason the date and the letterhead are read off the front."""
        pages = [{"page": n, "text": "x"} for n in (1, 2)]
        pages.append({"page": 3, "text": "www.deltadentalins.com"})
        assert identify(pages, [DENTAL]) == []

    def test_one_hit_per_thing(self) -> None:
        """A phone printed in the header and again in the footer is one
        sender, not two."""
        found = identify(
            _pages("(845) 486-3000", "Questions? (845) 486-3000"), [DSS]
        )
        assert len(found) == 1


class TestWhatTheAppAlreadyKnows:
    """The index itself: every identifying string on file, gathered
    once. Four sources because a household does not think of them as
    four - it thinks "who is this from and what is it about"."""

    @pytest.mark.asyncio
    async def test_it_gathers_masks_websites_phones_and_routing_numbers(
        self, async_db_session
    ) -> None:
        from app.services.documents.domains.reading.identity import known_strings
        from app.services.finance.models import FinanceInstitution
        from app.services.finance.service import FinanceService
        from app.services.matters.service import PartyService
        from tests.services._finance_factories import seed_account

        account = await seed_account(FinanceService(async_db_session), name="Card")
        account.mask = "3639"
        async_db_session.add(account)
        await PartyService(async_db_session).create(
            name="Delta Dental",
            kind="organization",
            contact={
                "website": "https://www.deltadentalins.com",
                "phone": "1-888-282-8784",
            },
        )
        bank = FinanceInstitution(
            name="HVCU",
            normalized_name="hvcu",
            provider="manual",
            routing_number="221979363",
        )
        async_db_session.add(bank)
        await async_db_session.flush()

        index = {(one.kind, one.how, one.value) for one in await known_strings(
            async_db_session
        )}

        assert ("account", "mask", "3639") in index
        assert ("party", "domain", "https://www.deltadentalins.com") in index
        assert ("party", "phone", "1-888-282-8784") in index
        assert ("institution", "routing", "221979363") in index

    @pytest.mark.asyncio
    async def test_a_contact_with_nothing_printable_is_not_in_it(
        self, async_db_session
    ) -> None:
        from app.services.documents.domains.reading.identity import known_strings
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Somebody", kind="person"
        )
        await async_db_session.flush()

        assert not [
            one for one in await known_strings(async_db_session)
            if one.kind == "party" and one.target == party.id
        ]
