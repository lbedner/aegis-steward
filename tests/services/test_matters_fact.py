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
        """"It says so in the letter" is not provenance if nobody can
        open the letter."""
        subject = await _james(async_db_session)
        with pytest.raises(ValueError, match="needs the document"):
            await FactService(async_db_session).record(
                subject_party_id=subject,
                attribute="resource_value",
                value_cents=100,
                provenance="document",
            )
