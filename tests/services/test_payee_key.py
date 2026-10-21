"""One payee key (#542): the provider's name for a payee, else the bank's
wording without the bank's own words, stored on each row with its version.

The key used to be the wording's first four words. A bank feed spends
two of them on "POS DEBIT" and a third on the store number, so every
Dunkin' store was a payee of its own, and naming one taught nothing
about the next. The feed names the payee itself ("Dunkin'"), and every
charge both feeds brought teaches what that name means. A better key
re-keys the rows it changes, and what was stored under the old one -
a budget line, the payee memory - follows them.
"""

from datetime import date

import pytest
from sqlmodel import col, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.domains.ledger import payee_aliases
from app.services.finance.domains.planning.budgets import queries as budget_queries
from app.services.finance.models import FinanceMerchantAlias, FinanceTransaction
from app.services.finance.service import FinanceService
from app.services.finance.utils import PAYEE_KEY_VERSION, transaction_payee_key
from tests.services._finance_factories import seed_account

DUNKIN_ANYTOWN = "POS DEBIT DUNKIN #346509 Q35 ANYTOWN NY"
DUNKIN_RIVERSIDE = "POS DEBIT DUNKIN #349340 Q35 RIVERSIDE NY"


class TestTheKey:
    def test_the_feeds_name_keys_every_store_alike(self) -> None:
        keys = {
            transaction_payee_key(None, wording, "Dunkin'")
            for wording in (DUNKIN_ANYTOWN, DUNKIN_RIVERSIDE)
        }
        assert keys == {"DUNKIN"}

    def test_wording_alone_drops_the_banks_words_and_numbers(self) -> None:
        wallet = transaction_payee_key(None, "AplPay EXXONMOBIL ANYTOWN NY", None)
        card = transaction_payee_key(
            None, "POS DEBIT EXXONMOBIL 9748 ANYTOWN NY 10/16", None
        )
        assert wallet == card == "EXXONMOBIL ANYTOWN NY"

    def test_an_exports_row_keys_on_the_name_it_carries(self) -> None:
        assert transaction_payee_key(None, "Dunkin Donuts", "Dunkin Donuts") == (
            "DUNKIN DONUTS"
        )

    def test_a_reference_in_the_feeds_name_stays_out(self) -> None:
        keys = {
            transaction_payee_key(None, "ZELLE PAYMENT TO PAT DOE", name)
            for name in (
                "Zelle Transfer to Pat Doe Jpm99ct9ip9q",
                "Zelle Transfer to Pat Doe Jpm99cr4nhd5",
            )
        }
        assert keys == {"ZELLE TRANSFER TO PAT"}

    def test_a_fee_and_a_withdrawal_stay_two(self) -> None:
        fee = transaction_payee_key(None, "NON-CHASE ATM FEE-WITH", "ATM Fee")
        cash = transaction_payee_key(
            None, "NON-CHASE ATM WITHDRAW 241586 10/161546 US9", "ATM Withdrawal"
        )
        assert fee != cash


async def _bank_row(
    svc: FinanceService, account_id: int, wording: str, merchant_id: int | None = None
) -> FinanceTransaction:
    return await svc.create_transaction(
        owner_user_id=1,
        account_id=account_id,
        amount=-500,
        txn_date=date(2026, 10, 13),
        name="Dunkin'",
        original_description=wording,
        merchant_id=merchant_id,
    )


class TestTheRowKeepsItsKey:
    @pytest.mark.asyncio
    async def test_a_saved_row_carries_its_key_and_its_version(
        self, svc: FinanceService
    ) -> None:
        account = await seed_account(svc)
        row = await _bank_row(svc, account.id, DUNKIN_ANYTOWN)
        assert (row.payee_key, row.payee_key_version) == ("DUNKIN", PAYEE_KEY_VERSION)


class TestTheFeedsNameIsLearned:
    @pytest.mark.asyncio
    async def test_a_charge_both_feeds_brought_names_every_store_after_it(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        """The export called it Dunkin Donuts, the bank Dunkin' at one
        store; the bank's next Dunkin' is named, whichever store."""
        account = await seed_account(svc)
        payee = await svc.create_merchant("Dunkin Donuts", owner_user_id=1)
        export = await svc.create_transaction(
            owner_user_id=1,
            account_id=account.id,
            amount=-500,
            txn_date=date(2026, 9, 2),
            name="Dunkin Donuts",
            merchant_id=payee.id,
        )
        bank = await _bank_row(svc, account.id, DUNKIN_ANYTOWN)
        await payee_aliases.learn_from_pairs(async_db_session, [(export, bank)])

        asked = (transaction_payee_key(None, DUNKIN_RIVERSIDE, "Dunkin'"), "Dunkin'")
        found = await payee_aliases.payees_for(
            async_db_session, [asked], owner_user_id=1
        )
        assert found == {asked: payee.id}


class TestANewKeyReKeysWhatWasStored:
    @pytest.mark.asyncio
    @pytest.mark.queryspy(threshold=3)  # each line is written and read back
    async def test_a_budget_line_and_the_memory_follow_their_rows(
        self, svc: FinanceService, async_db_session: AsyncSession
    ) -> None:
        old = "POS DEBIT DUNKIN 346509"  # the first four words, as it was
        account = await seed_account(svc)
        payee = await svc.create_merchant("Dunkin Donuts", owner_user_id=1)
        row = await _bank_row(svc, account.id, DUNKIN_ANYTOWN, merchant_id=payee.id)
        # As the row stood before this key: the update skips the ORM, as a
        # row stored by the older version never went through it.
        await async_db_session.exec(
            update(FinanceTransaction)
            .where(col(FinanceTransaction.id) == row.id)
            .values(payee_key=old, payee_key_version=0)
        )
        line = await svc.upsert_budget_line(
            owner_user_id=1,
            period_month=None,
            category_id=None,
            payee_key=old,
            payee_label="Dunkin'",
            allocated_amount=5_000,
        )

        await svc.recompute_payee_aliases(owner_user_id=1)

        await async_db_session.refresh(row)
        assert (row.payee_key, row.payee_key_version) == ("DUNKIN", PAYEE_KEY_VERSION)
        stored = await budget_queries.budget_line_by_id(
            async_db_session, line.id, owner_user_id=1
        )
        assert stored is not None and stored.payee_key == "DUNKIN"
        aliases = (await async_db_session.exec(select(FinanceMerchantAlias))).all()
        assert {a.normalized_alias: a.merchant_id for a in aliases} == {
            "DUNKIN": payee.id
        }


def test_a_deploy_re_keys_as_it_starts() -> None:
    """A newer key moves the rows, the budget lines and the memory as the
    deploy starts, not at the night's run."""
    from app.services.finance.scheduled_jobs import JOBS

    (filing,) = [job for job in JOBS if job.id == "finance_file_blank_rows"]
    assert filing.at_start
