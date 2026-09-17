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
from tests._session import opens


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


class TestThePlanProposesThePolicy:
    @pytest.mark.asyncio
    async def test_the_card_names_everyone_and_every_term(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.insurance.changes import (
            PolicyCreatePayload,
            policy_create_describe,
            policy_create_execute,
        )

        insurer, marisa, leonard = await _insured(async_db_session)
        payload = PolicyCreatePayload(
            insurer_party_id=insurer,
            covered_party_ids=[marisa, leonard],
            kind="dental",
            name="Delta Dental PPO Premium Plan",
            group_id="18822-10007",
            effective_on=date(2026, 8, 1),
            terms={"Annual maximum": "$2,000 per member"},
        )
        said = {
            r.label: r.value
            for r in await policy_create_describe(async_db_session, payload, None)
        }
        assert said["Insurer"] == "Delta Dental of New York"
        assert said["Covers"] == "Marisa Testcase, Leonard Testcase"
        assert said["Group ID"] == "18822-10007"
        assert said["Annual maximum"] == "$2,000 per member"

        made = await policy_create_execute(async_db_session, payload, None)
        await async_db_session.commit()
        policy = await InsuranceService(async_db_session).get_policy(made["policy_id"])
        assert policy.covered_party_ids == [marisa, leonard]

    @pytest.mark.asyncio
    async def test_an_insurer_nobody_filed_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.insurance.changes import (
            PolicyCreatePayload,
            policy_create_execute,
        )

        with pytest.raises(ValueError, match="contact"):
            await policy_create_execute(
                async_db_session,
                PolicyCreatePayload(
                    insurer_party_id=999999, covered_party_ids=[1], kind="dental"
                ),
                None,
            )


class TestTheEobProposesTheClaim:
    @pytest.mark.asyncio
    async def test_the_card_shows_the_four_figures_and_the_eob_lands(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.ai.domains.chat.pastes import store_document
        from app.services.documents.service import DocumentService
        from app.services.insurance.changes import (
            ClaimRecordPayload,
            claim_record_describe,
            claim_record_execute,
        )

        insurer, marisa, _ = await _insured(async_db_session)
        service = InsuranceService(async_db_session)
        policy = await service.create_policy(
            insurer_party_id=insurer,
            covered_party_ids=[marisa],
            kind="dental",
            name="Delta Dental PPO Premium Plan",
        )
        document = await DocumentService(async_db_session).ingest(
            b"%PDF-1.4 eob", title="ClaimStatement.pdf"
        )
        paste = await store_document(
            "0", int(document.id), "ClaimStatement.pdf", 11_049, async_db_session
        )
        payload = ClaimRecordPayload(
            policy_id=int(policy.id),
            covered_party_id=marisa,
            service_on=date(2026, 8, 12),
            claim_number="23548895152718",
            billed_cents=60500,
            allowed_cents=35600,
            insurer_paid_cents=21000,
            patient_owes_cents=14600,
            paste_id=str(paste["id"]),
        )
        said = {
            r.label: r.value
            for r in await claim_record_describe(async_db_session, payload, None)
        }
        assert said["Policy"] == "Delta Dental PPO Premium Plan"
        assert said["For"] == "Marisa Testcase"
        assert said["You owe the provider"] == "$146.00"
        assert said["EOB"] == "ClaimStatement.pdf"

        made = await claim_record_execute(async_db_session, payload, None)
        await async_db_session.commit()
        claim = (await service.claims_of(int(policy.id)))[0]
        assert claim.id == made["claim_id"]
        assert claim.document_id == document.id

    def test_the_eob_is_one_thing(self) -> None:
        from pydantic import ValidationError

        from app.services.insurance.changes import ClaimRecordPayload

        with pytest.raises(ValidationError):
            ClaimRecordPayload(
                policy_id=1,
                covered_party_id=1,
                service_on=date(2026, 8, 12),
                paste_id="x",
                document_id=2,
            )


class TestIllianaReadsPolicies:
    @pytest.mark.asyncio
    async def test_the_tool_lists_policies_with_their_claims(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.ai.domains.chat.tools import registered_tool_names
        from app.services.insurance import ai_tools

        monkeypatch.setattr(ai_tools, "get_async_session", opens(async_db_session))
        insurer, marisa, _ = await _insured(async_db_session)
        service = InsuranceService(async_db_session)
        policy = await service.create_policy(
            insurer_party_id=insurer, covered_party_ids=[marisa], kind="dental"
        )
        await service.record_claim(
            policy_id=int(policy.id),
            covered_party_id=marisa,
            service_on=date(2026, 8, 12),
            patient_owes_cents=14600,
        )
        await async_db_session.commit()

        assert "policies" in registered_tool_names()
        told = await ai_tools.policies()
        mine = next(p for p in told["policies"] if p["id"] == policy.id)
        assert mine["insurer"] == "Delta Dental of New York"
        assert mine["patient_owes_cents"] == 14600
        assert mine["claims"][0]["service_on"] == "2026-08-12"

    def test_the_prompt_says_the_policy_holds_the_figures(self) -> None:
        from app.services.finance.domains.detection.analyst.prompts import (
            FINANCE_CHAT_SYSTEM_PROMPT,
        )

        assert "policy.create" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "claim.record" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "never in a contact's note" in FINANCE_CHAT_SYSTEM_PROMPT


class TestAClaimIsPaid:
    """The EOB says what is owed; the ledger shows it leaving. Linking
    the two is what turns "owed to providers" into a number that goes
    down when you pay."""

    async def _claim(self, db: AsyncSession) -> tuple[int, int]:
        insurer, marisa, _ = await _insured(db)
        service = InsuranceService(db)
        policy = await service.create_policy(
            insurer_party_id=insurer, covered_party_ids=[marisa], kind="dental"
        )
        claim = await service.record_claim(
            policy_id=int(policy.id),
            covered_party_id=marisa,
            service_on=date(2026, 8, 12),
            patient_owes_cents=14600,
        )
        await db.commit()
        return int(policy.id), int(claim.id)

    @pytest.mark.asyncio
    async def test_candidates_are_charges_near_the_amount_and_the_visit(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.service import FinanceService
        from tests.services._finance_factories import seed_account, seed_txn

        policy_id, claim_id = await self._claim(async_db_session)
        finance = FinanceService(async_db_session)
        amex = await seed_account(finance, name="AMEX")
        hit = await seed_txn(
            finance, int(amex.id), -14600, date(2026, 8, 12), name="Endodontics"
        )
        await seed_txn(finance, int(amex.id), -14600, date(2026, 3, 1), name="Long ago")
        await seed_txn(
            finance, int(amex.id), -9900, date(2026, 8, 12), name="Wrong amount"
        )
        await seed_txn(finance, int(amex.id), 14600, date(2026, 8, 12), name="A refund")
        await async_db_session.commit()

        service = InsuranceService(async_db_session)
        found = await service.claim_candidates(claim_id, owner_user_id=1)
        assert [t.id for t in found] == [hit.id]

    @pytest.mark.asyncio
    async def test_marking_paid_takes_it_off_the_total(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.finance.service import FinanceService
        from app.services.insurance.changes import (
            ClaimPaidPayload,
            claim_paid_describe,
            claim_paid_execute,
        )
        from tests.services._finance_factories import seed_account, seed_txn

        policy_id, claim_id = await self._claim(async_db_session)
        finance = FinanceService(async_db_session)
        amex = await seed_account(finance, name="AMEX")
        charge = await seed_txn(
            finance, int(amex.id), -14600, date(2026, 8, 12), name="Endodontics"
        )
        await async_db_session.commit()
        service = InsuranceService(async_db_session)
        assert await service.patient_owes_total(policy_id) == 14600

        payload = ClaimPaidPayload(claim_id=claim_id, transaction_id=int(charge.id))
        said = {
            r.label: r.value
            for r in await claim_paid_describe(async_db_session, payload, 1)
        }
        assert said["Claim"].startswith("2026-08-12")
        assert "$146.00" in said["Claim"]
        assert "Endodontics" in said["Payment"]

        await claim_paid_execute(async_db_session, payload, 1)
        await async_db_session.commit()
        claim = (await service.claims_of(policy_id))[0]
        assert claim.paid_transaction_id == charge.id
        assert await service.patient_owes_total(policy_id) == 0

    @pytest.mark.asyncio
    async def test_a_transaction_nobody_has_is_refused(
        self, async_db_session: AsyncSession
    ) -> None:
        from app.services.insurance.changes import ClaimPaidPayload, claim_paid_execute

        _policy_id, claim_id = await self._claim(async_db_session)
        with pytest.raises(ValueError, match="ransaction"):
            await claim_paid_execute(
                async_db_session,
                ClaimPaidPayload(claim_id=claim_id, transaction_id=999_999),
                1,
            )
