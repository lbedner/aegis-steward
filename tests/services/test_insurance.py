"""Insurance: a policy and its claims have a home.

Six Delta Dental documents were read in one turn and every figure in
them - group ID, enrollee ID, deductible, maximums, an EOB's four
amounts - ended up in a note on the contact, because nothing else could
hold them. A note cannot be queried, totalled or attached to.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.insurance.models import CLAIM_STATUSES, POLICY_KINDS
from app.services.insurance.service import InsuranceService
from app.services.matters.service import PartyService


async def _insured(db: AsyncSession) -> tuple[int, int, int]:
    parties = PartyService(db)
    insurer = await parties.create(name="Delta Dental of New York", kind="organization")
    marisa = await parties.create(name="Marisa Testcase", kind="person")
    leonard = await parties.create(name="Leonard Testcase", kind="person")
    return int(insurer.id), int(marisa.id), int(leonard.id)


class TestAPolicy:
    @pytest.mark.asyncio
    async def test_it_holds_what_the_plan_documents_say(
        self, async_db_session: AsyncSession
    ) -> None:
        insurer, marisa, leonard = await _insured(async_db_session)
        service = InsuranceService(async_db_session)

        policy = await service.create_policy(
            insurer_party_id=insurer,
            covered_party_ids=[marisa, leonard],
            kind="dental",
            name="Delta Dental PPO Premium Plan",
            policy_number="6448960775",
            member_id="127396822501",
            group_id="18822-10007",
            effective_on=date(2026, 8, 1),
            terms={
                "Deductible": "$50 per person, $150 family",
                "Annual maximum": "$2,000 per member",
                "Major services": "50% after deductible, 12-month wait",
            },
        )
        await async_db_session.commit()

        found = await service.get_policy(policy.id)
        assert found is not None
        assert found.covered_party_ids == [marisa, leonard]
        assert found.terms["Annual maximum"] == "$2,000 per member"
        assert found.premium_stream_id is None

        assert [p.id for p in await service.policies_of(insurer)] == [policy.id]
        assert [p.id for p in await service.policies_covering(leonard)] == [policy.id]
        assert await service.policies_covering(insurer) == []

    @pytest.mark.asyncio
    async def test_a_kind_nobody_defined_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        insurer, marisa, _ = await _insured(async_db_session)
        with pytest.raises(ValueError, match="One of"):
            await InsuranceService(async_db_session).create_policy(
                insurer_party_id=insurer,
                covered_party_ids=[marisa],
                kind="pet",
                name="Pet plan",
            )
        assert "dental" in POLICY_KINDS


class TestAClaim:
    @pytest.mark.asyncio
    async def test_the_four_figures_survive_and_add_up(
        self, async_db_session: AsyncSession
    ) -> None:
        """An EOB says billed, allowed, insurer paid, patient owes. The
        last is money owed to the PROVIDER, not the insurer, and the
        policy can say how much of that is outstanding."""
        insurer, marisa, _ = await _insured(async_db_session)
        service = InsuranceService(async_db_session)
        dentist = await PartyService(async_db_session).create(
            name="Magdalena Goralczyk DDS", kind="person"
        )
        policy = await service.create_policy(
            insurer_party_id=insurer, covered_party_ids=[marisa], kind="dental"
        )

        claim = await service.record_claim(
            policy_id=policy.id,
            covered_party_id=marisa,
            service_on=date(2026, 8, 12),
            provider_party_id=int(dentist.id),
            claim_number="23548895152718",
            status="processed",
            billed_cents=60500,
            allowed_cents=35600,
            insurer_paid_cents=21000,
            patient_owes_cents=14600,
        )
        second = await service.record_claim(
            policy_id=policy.id,
            covered_party_id=marisa,
            service_on=date(2026, 9, 2),
            billed_cents=12000,
            allowed_cents=9000,
            insurer_paid_cents=9000,
            patient_owes_cents=0,
        )
        await async_db_session.commit()

        claims = await service.claims_of(policy.id)
        assert [c.id for c in claims] == [second.id, claim.id]
        assert claims[1].patient_owes_cents == 14600
        assert claims[1].document_id is None
        assert await service.patient_owes_total(policy.id) == 14600

    @pytest.mark.asyncio
    async def test_a_status_nobody_defined_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        insurer, marisa, _ = await _insured(async_db_session)
        service = InsuranceService(async_db_session)
        policy = await service.create_policy(
            insurer_party_id=insurer, covered_party_ids=[marisa], kind="dental"
        )
        with pytest.raises(ValueError, match="One of"):
            await service.record_claim(
                policy_id=policy.id,
                covered_party_id=marisa,
                service_on=date(2026, 8, 12),
                status="lost",
            )
        assert "denied" in CLAIM_STATUSES

    @pytest.mark.asyncio
    async def test_a_claim_on_no_policy_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        insurer, marisa, _ = await _insured(async_db_session)
        with pytest.raises(ValueError, match="policy"):
            await InsuranceService(async_db_session).record_claim(
                policy_id=999999, covered_party_id=marisa, service_on=date(2026, 8, 12)
            )
