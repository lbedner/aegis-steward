"""A loan can be told what it costs.

``FinanceLiabilityDetail`` held a loan's terms all along and the app
read them everywhere - the accounts page draws "Due Jul 15 · min $35.00"
from them, and ``accounts()`` hands the whole block to the assistant -
but nothing wrote them except a Plaid sync. So a pool loan from a lender
with no bank connection could be created, could hold a balance, and
could never be told its own rate. Asked what was left on it and at what
percent, the assistant could only say the connected accounts did not
include it.
"""

from datetime import date

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.writes.terms import (
    LoanTermsPayload,
    loan_terms_describe,
    loan_terms_execute,
)
from app.services.finance.service import FinanceService
from tests.services._finance_factories import seed_account

OWNER = 1


async def _loan(svc: FinanceService, name: str = "GreenSky") -> int:
    account = await seed_account(
        svc, name=name, account_type="loan", classification="liability"
    )
    return int(account.id)


class TestRecordingTheTerms:
    @pytest.mark.asyncio
    async def test_the_stated_terms_land_on_the_account(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account_id = await _loan(svc)

        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(
                account_id=account_id,
                outstanding_balance=987_366,
                interest_rate_bps=799,
                minimum_payment_amount=22_400,
            ),
            OWNER,
        )
        await async_db_session.commit()

        from app.services.finance.domains.ledger.accounts import liability_details

        detail = (await liability_details(async_db_session, [account_id]))[account_id]
        assert detail.outstanding_balance == 987_366
        assert detail.interest_rate_bps == 799
        assert detail.minimum_payment_amount == 22_400

    @pytest.mark.asyncio
    async def test_a_later_statement_sets_only_what_it_names(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Terms arrive a few at a time. A card that had to restate every
        field would make the second statement erase the first."""
        account_id = await _loan(svc)
        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(
                account_id=account_id,
                outstanding_balance=987_366,
                interest_rate_bps=799,
            ),
            OWNER,
        )

        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(
                account_id=account_id, next_payment_due_date=date(2026, 10, 12)
            ),
            OWNER,
        )
        await async_db_session.commit()

        from app.services.finance.domains.ledger.accounts import liability_details

        detail = (await liability_details(async_db_session, [account_id]))[account_id]
        assert detail.next_payment_due_date == date(2026, 10, 12)
        assert detail.interest_rate_bps == 799

    @pytest.mark.asyncio
    async def test_an_asset_has_no_loan_terms(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account = await seed_account(svc, name="Checking")

        with pytest.raises(ValueError, match="only a debt"):
            await loan_terms_execute(
                async_db_session,
                LoanTermsPayload(account_id=int(account.id), interest_rate_bps=799),
                OWNER,
            )

    def test_an_empty_change_is_refused(self) -> None:
        """A card with nothing on it is one nobody can approve."""
        with pytest.raises(ValueError, match="at least one term"):
            LoanTermsPayload(account_id=1)


class TestTheCard:
    @pytest.mark.asyncio
    async def test_it_reads_in_the_units_a_person_uses(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Basis points are the column's unit, not the reader's."""
        account_id = await _loan(svc)

        rows = await loan_terms_describe(
            async_db_session,
            LoanTermsPayload(
                account_id=account_id,
                outstanding_balance=987_366,
                interest_rate_bps=799,
                loan_term_months=84,
            ),
            OWNER,
        )

        shown = {r.label: r.value for r in rows}
        assert shown["Account"] == "GreenSky"
        assert shown["Balance owed"] == "$9,873.66"
        assert shown["Interest rate"] == "7.99%"
        assert shown["Term"] == "84 months"

    @pytest.mark.asyncio
    async def test_a_replacement_says_what_it_replaces(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Overwriting a rate is a different decision from stating one."""
        account_id = await _loan(svc)
        await loan_terms_execute(
            async_db_session,
            LoanTermsPayload(account_id=account_id, interest_rate_bps=799),
            OWNER,
        )

        rows = await loan_terms_describe(
            async_db_session,
            LoanTermsPayload(account_id=account_id, interest_rate_bps=2_999),
            OWNER,
        )

        assert [r.value for r in rows if r.label == "Interest rate"] == [
            "7.99% → 29.99%"
        ]

    @pytest.mark.asyncio
    async def test_the_card_lists_only_what_is_being_set(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        account_id = await _loan(svc)

        rows = await loan_terms_describe(
            async_db_session,
            LoanTermsPayload(account_id=account_id, interest_rate_bps=799),
            OWNER,
        )

        assert [r.label for r in rows] == ["Account", "Interest rate"]


class TestItIsProposable:
    def test_the_queue_knows_the_change_type(self) -> None:
        from app.services.finance.domains.writes import executors  # noqa: F401
        from app.services.finance.domains.writes.registry import (
            registered_change_types,
        )

        assert "account.loan_terms" in registered_change_types()

    def test_a_statement_screenshot_is_terms_not_line_items(self) -> None:
        """The only image rule was "orders become splits", so a lender's
        portal would be read through an order-shaped lens: no line items
        to find, and nothing proposed."""
        from app.services.finance.domains.detection.analyst.prompts import (
            FINANCE_CHAT_SYSTEM_PROMPT,
        )

        assert "Not every screenshot is an order" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "source of TERMS, not of line items" in FINANCE_CHAT_SYSTEM_PROMPT
        # A portal's own wording is not a figure you recognise.
        assert '"Account Interest"' in FINANCE_CHAT_SYSTEM_PROMPT

    def test_the_prompt_says_the_rate_is_basis_points(self) -> None:
        """7.99 sent as a percentage would be filed as 0.0799%."""
        from app.services.finance.domains.detection.analyst.prompts import (
            FINANCE_CHAT_SYSTEM_PROMPT,
        )

        assert "account.loan_terms" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "7.99% is 799" in FINANCE_CHAT_SYSTEM_PROMPT


class TestAddingTheAccountItself:
    """A lender with no bank connection is invisible until someone says
    it exists. Live, that was a dead end reached twice in one
    conversation: "I can't create a new account in the app yet - the
    available change types only let me update terms on an EXISTING loan
    account." The terms were known; the account was not."""

    @pytest.mark.asyncio
    async def test_a_debt_is_created_owing_what_was_stated(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """A statement says what is OWED; the ledger holds a liability
        negative. The payload speaks the statement's language."""
        from app.services.finance.domains.writes.terms import (
            CreateAccountPayload,
            create_account_execute,
        )

        result = await create_account_execute(
            async_db_session,
            CreateAccountPayload(
                name="GreenSky", account_type="loan", current_balance=987_366
            ),
            OWNER,
        )
        await async_db_session.commit()

        from app.services.finance.domains.ledger.accounts import get_account

        account = await get_account(
            async_db_session, result["account_id"], owner_user_id=OWNER
        )
        assert account is not None
        assert account.classification == "liability"
        assert account.current_balance == -987_366

    @pytest.mark.asyncio
    async def test_the_institution_on_the_card_is_the_one_created(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The card says "Held with GreenSky" before approval, so
        approval has to deliver it - a card that promises something the
        write drops is worse than a card that never offered."""
        from app.services.finance.domains.writes.terms import (
            CreateAccountPayload,
            create_account_execute,
        )

        result = await create_account_execute(
            async_db_session,
            CreateAccountPayload(
                name="GreenSky loan", account_type="loan", institution="GreenSky"
            ),
            OWNER,
        )
        await async_db_session.commit()

        from app.services.finance.domains.ledger.accounts import get_account

        account = await get_account(
            async_db_session, result["account_id"], owner_user_id=OWNER
        )
        assert account is not None
        assert account.institution_id is not None
        assert result["institution"] == "GreenSky"

    @pytest.mark.asyncio
    async def test_the_same_name_twice_is_refused(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """Transactions attach to accounts and balances derive from them,
        so a duplicate is the expensive mistake."""
        from app.services.finance.domains.writes.terms import (
            CreateAccountPayload,
            create_account_execute,
        )

        await seed_account(
            svc, name="GreenSky", account_type="loan", classification="liability"
        )

        with pytest.raises(ValueError, match="already exists"):
            await create_account_execute(
                async_db_session,
                CreateAccountPayload(name="greensky", account_type="loan"),
                OWNER,
            )

    @pytest.mark.asyncio
    async def test_the_card_shows_what_the_ledger_already_has(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """No rule can know that two names are one debt, so the card
        shows the near-misses and the person approving decides."""
        from app.services.finance.domains.writes.terms import (
            CreateAccountPayload,
            create_account_describe,
        )

        await seed_account(
            svc,
            name="Anthony & Sylvan Pools Fairfield",
            account_type="loan",
            classification="liability",
        )

        rows = await create_account_describe(
            async_db_session,
            CreateAccountPayload(
                name="Anthony & Sylvan Pools loan", account_type="loan"
            ),
            OWNER,
        )

        shown = {r.label: r.value for r in rows}
        assert shown["New account"] == "Anthony & Sylvan Pools loan"
        assert shown["Kind"] == "loan · liability"
        assert "Anthony & Sylvan Pools Fairfield" in shown["You already have"]

    def test_an_unknown_type_is_refused(self) -> None:
        from app.services.finance.domains.writes.terms import CreateAccountPayload

        with pytest.raises(ValueError, match="is not an account type"):
            CreateAccountPayload(name="GreenSky", account_type="lone")

    def test_the_prompt_says_to_look_before_creating(self) -> None:
        """The duplicate is the mistake, and only the user can rule it
        out - "GreenSky" and "Anthony & Sylvan Pools" are one loan."""
        from app.services.finance.domains.detection.analyst.prompts import (
            FINANCE_CHAT_SYSTEM_PROMPT,
        )

        assert "account.create" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "Call accounts() FIRST" in FINANCE_CHAT_SYSTEM_PROMPT
        assert "SEPARATE card AFTER" in FINANCE_CHAT_SYSTEM_PROMPT
