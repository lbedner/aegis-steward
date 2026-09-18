"""Facts: a number is not an answer until it says where it came from.

ST-06's gate is two facts that disagree - a deposit and a benefit
letter, same subject, same attribute, same date - both standing, each
showing its source, with the document-derived one verified.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.facts import FactService, monthly_cents
from app.services.matters.service import PartyService


async def _james(db: AsyncSession) -> int:
    party = await PartyService(db).create(name="James Bedner", kind="person")
    await db.commit()
    assert party.id is not None
    return party.id


class TestAFact:
    @pytest.mark.asyncio
    async def test_two_sources_can_disagree_and_both_stand(
        self, async_db_session: AsyncSession
    ) -> None:
        """The ledger sees what landed; the letter says what was awarded
        before the premium came out. Recording that they disagree is the
        feature - deciding which is right is the reader's."""
        subject = await _james(async_db_session)
        facts = FactService(async_db_session)

        await facts.record(
            subject_party_id=subject,
            attribute="gross_income",
            label="Social security",
            value_cents=207500,
            period="month",
            as_of=date(2026, 8, 1),
            provenance="ledger",
            source_note="Deposit in the register",
        )
        await facts.record(
            subject_party_id=subject,
            attribute="gross_income",
            label="Social security",
            value_cents=218040,
            period="month",
            as_of=date(2026, 8, 1),
            provenance="document",
            document_id=77,
            page=1,
            verified=True,
        )
        await async_db_session.commit()

        standing = await facts.find(subject_party_id=subject)
        assert [(f.value_cents, f.provenance, f.verified) for f in standing] == [
            (218040, "document", True),
            (207500, "ledger", False),
        ]

    @pytest.mark.asyncio
    async def test_a_correction_supersedes_and_the_old_row_still_says_it(
        self, async_db_session: AsyncSession
    ) -> None:
        """What you told an agency last year is the one thing you may
        later have to defend, so a correction never overwrites."""
        subject = await _james(async_db_session)
        facts = FactService(async_db_session)
        first = await facts.record(
            subject_party_id=subject,
            attribute="account_balance",
            label="Eleanor incidental",
            value_cents=41200,
            as_of=date(2026, 8, 1),
        )
        await async_db_session.commit()

        fresh = await facts.supersede(first.id, value_cents=48750, verified=True)
        await async_db_session.commit()

        assert [f.value_cents for f in await facts.find(subject_party_id=subject)] == [
            48750
        ]
        old = await facts.get(first.id)
        assert old is not None
        assert old.value_cents == 41200
        assert old.superseded_by_id == fresh.id

    @pytest.mark.asyncio
    async def test_a_rate_is_stored_as_quoted_and_read_as_a_month(
        self, async_db_session: AsyncSession
    ) -> None:
        """The portal quotes a day, the county asks for a month.
        Multiplying on the way IN would file our arithmetic as their
        quotation."""
        subject = await _james(async_db_session)
        facts = FactService(async_db_session)

        fact = await facts.record(
            subject_party_id=subject,
            attribute="gross_income",
            label="IBEW pension",
            value_cents=5000,
            period="day",
            as_of=date(2026, 8, 1),
            provenance="stated",
            source_note="Read off the pension portal",
        )
        await async_db_session.commit()

        assert fact.value_cents == 5000
        assert fact.period == "day"
        assert monthly_cents(fact.value_cents, fact.period) == 152188

    @pytest.mark.asyncio
    async def test_a_fact_with_nothing_in_it_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        subject = await _james(async_db_session)
        with pytest.raises(ValueError, match="figure"):
            await FactService(async_db_session).record(
                subject_party_id=subject, attribute="gross_income"
            )

    @pytest.mark.asyncio
    async def test_a_document_fact_needs_the_document(
        self, async_db_session: AsyncSession
    ) -> None:
        """ "It says so in the letter" is not provenance if nobody can
        open the letter."""
        subject = await _james(async_db_session)
        with pytest.raises(ValueError, match="needs the document"):
            await FactService(async_db_session).record(
                subject_party_id=subject,
                attribute="resource_value",
                value_cents=100,
                provenance="document",
            )


class TestAFactAsAProposal:
    """Illiana can put a figure in front of you. She cannot file one."""

    @pytest.mark.asyncio
    async def test_the_card_says_whose_what_and_how_it_is_known(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import (
            RecordFactPayload,
            record_fact_describe,
            record_fact_execute,
        )

        subject = await _james(async_db_session)
        payload = RecordFactPayload(
            subject_party_id=subject,
            attribute="gross_income",
            provenance="stated",
            label="IBEW pension",
            value_cents=5000,
            period="day",
            source_note="Read off the pension portal",
        )

        card = await record_fact_describe(async_db_session, payload, None)
        said = {row.label: row.value for row in card}

        assert said["About"] == "James Bedner"
        assert said["Gross income"] == "IBEW pension"
        # The rate as quoted, and our arithmetic marked as ours.
        assert said["Figure"] == "$50.00 a day (about $1,521.88 a month)"
        assert "stated" in said["How it is known"]
        assert "pension portal" in said["How it is known"]

        await record_fact_execute(async_db_session, payload, None)
        await async_db_session.commit()

        [recorded] = await FactService(async_db_session).find(subject_party_id=subject)
        assert recorded.value_cents == 5000
        # Approving a card is approving what was READ, never a claim
        # that somebody opened the source and checked it.
        assert recorded.verified is False

    @pytest.mark.asyncio
    async def test_an_attribute_nobody_defined_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from pydantic import ValidationError

        from app.services.matters.changes import RecordFactPayload

        with pytest.raises(ValidationError):
            RecordFactPayload(
                subject_party_id=1, attribute="vibes", provenance="stated"
            )


class TestWhatAPlaceSays:
    @pytest.mark.asyncio
    async def test_facts_from_a_source_are_found_apart_from_facts_about_a_subject(
        self, async_db_session: AsyncSession
    ) -> None:
        """NYSLRS says what it pays James; James has money said about
        him. Two different questions of one table."""
        subject = await _james(async_db_session)
        place = await PartyService(async_db_session).create(
            name="NYSLRS", kind="organization", owner_user_id=None
        )
        facts = FactService(async_db_session)
        await facts.record(
            subject_party_id=subject,
            attribute="gross_income",
            provenance="stated",
            value_cents=1000,
            period="day",
            source_party_id=place.id,
        )
        await facts.record(
            subject_party_id=subject,
            attribute="account_balance",
            provenance="stated",
            value_cents=250000,
        )
        await async_db_session.commit()

        says = await facts.find(source_party_id=place.id)
        about = await facts.find(subject_party_id=subject)

        assert [f.attribute for f in says] == ["gross_income"]
        assert {f.attribute for f in about} == {"gross_income", "account_balance"}


class TestAFigureMustSayWhereItWasRead:
    """A figure without its provenance is not fit to put on a government
    form, and "provenance: document" is not provenance - it is a claim
    that provenance exists somewhere.

    The service already refused a document-derived fact with no
    document. It did not insist on the PAGE or the line, so a statement
    could be cited as a whole: nine pages, one of which says $1,004.93,
    and no way to know which without opening it.
    """

    def test_a_document_fact_needs_its_page(self) -> None:
        from pydantic import ValidationError

        from app.services.matters.changes import RecordFactPayload

        with pytest.raises(ValidationError, match="page"):
            RecordFactPayload(
                subject_party_id=1,
                attribute="gross_income",
                provenance="document",
                document_id=8,
                value_cents=217894,
                period="month",
                source_note="Base Benefit $1,089.47",
            )

    def test_a_document_fact_needs_the_line_it_was_read_from(self) -> None:
        from pydantic import ValidationError

        from app.services.matters.changes import RecordFactPayload

        with pytest.raises(ValidationError, match="read"):
            RecordFactPayload(
                subject_party_id=1,
                attribute="gross_income",
                provenance="document",
                document_id=8,
                page=1,
                value_cents=217894,
                period="month",
            )

    def test_a_cited_document_fact_is_accepted(self) -> None:
        from app.services.matters.changes import RecordFactPayload

        payload = RecordFactPayload(
            subject_party_id=1,
            attribute="gross_income",
            provenance="document",
            document_id=8,
            page=1,
            value_cents=217894,
            period="month",
            source_note="Base Benefit $1,089.47 + Pension Reserve $746.80",
        )
        assert payload.page == 1

    def test_a_stated_fact_needs_none_of_that(self) -> None:
        """Somebody saying a number out loud has no page. The rule is
        about what "document" CLAIMS, not about every fact."""
        from app.services.matters.changes import RecordFactPayload

        payload = RecordFactPayload(
            subject_party_id=1,
            attribute="gross_income",
            provenance="stated",
            value_cents=217894,
            period="month",
        )
        assert payload.page is None


class TestTheSameFigureTwice:
    """Live: the NYSLRS gross benefit was recorded on 16 September and
    again on 18 September - same subject, same attribute, same value,
    same document, same page - and nothing said a word. A ledger that
    answers "what is his gross income" with two identical rows has made
    the question harder than it was on paper.

    Not refused: a figure really can be recorded twice, from two
    statements, or restated after a change. Said out loud on the card,
    the way account.create says "You already have".
    """

    @pytest.mark.asyncio
    async def test_the_card_says_when_this_is_already_on_file(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import (
            RecordFactPayload,
            record_fact_describe,
            record_fact_execute,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Twice Testcase", kind="person"
        )
        payload = RecordFactPayload(
            subject_party_id=party.id,
            attribute="gross_income",
            provenance="stated",
            value_cents=217894,
            period="month",
        )
        await record_fact_execute(async_db_session, payload, None)
        await async_db_session.flush()

        said = {
            row.label: row.value
            for row in await record_fact_describe(async_db_session, payload, None)
        }
        assert "already" in " ".join(said).casefold()
        assert "$2,178.94" in said["Already on file"]

    @pytest.mark.asyncio
    async def test_a_different_figure_is_not_flagged(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.matters.changes import (
            RecordFactPayload,
            record_fact_describe,
            record_fact_execute,
        )
        from app.services.matters.service import PartyService

        party = await PartyService(async_db_session).create(
            name="Different Testcase", kind="person"
        )
        await record_fact_execute(
            async_db_session,
            RecordFactPayload(
                subject_party_id=party.id,
                attribute="gross_income",
                provenance="stated",
                value_cents=100493,
                period="month",
            ),
            None,
        )
        await async_db_session.flush()

        said = {
            row.label: row.value
            for row in await record_fact_describe(
                async_db_session,
                RecordFactPayload(
                    subject_party_id=party.id,
                    attribute="gross_income",
                    provenance="stated",
                    value_cents=217894,
                    period="month",
                ),
                None,
            )
        }
        assert "Already on file" not in said
