"""The bill reminder: comms' first finance use.

A scheduled mail listing what is due in the next few days. It stays
silent unless someone configured a recipient, and on a day with nothing
due, because an empty reminder is worse than none.
"""

from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.services.finance import jobs
from app.services.finance.service import FinanceService


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every mail the job tried to send."""
    sent: list[dict[str, Any]] = []

    async def _send(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr("app.services.comms.email.send_email_simple", _send)
    return sent


@pytest.fixture
def job_session(
    async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job opens its own session; point it at the test's."""

    @asynccontextmanager
    async def _session():  # noqa: ANN202
        yield async_db_session

    monkeypatch.setattr(jobs, "get_async_session", _session)


@pytest.fixture
async def a_bill(async_db_session: AsyncSession) -> None:
    """One account with money in it and one bill due in three days."""
    finance = FinanceService(async_db_session)
    account = await finance.create_manual_account(
        name="Checking", account_type="checking", classification="asset"
    )
    assert account.id is not None
    await finance.update_account_balance(account.id, current_balance=100_000)
    await finance.create_recurring_stream(
        owner_user_id=None,
        name="Rent",
        direction="outflow",
        frequency="monthly",
        expected_amount=150_000,
        next_expected_date=date.today() + timedelta(days=3),
        account_id=account.id,
    )
    await async_db_session.commit()


async def test_says_nothing_until_someone_is_named(
    outbox: list[dict[str, Any]],
    job_session: None,
    a_bill: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", None)
    await jobs.finance_bill_due_email_job()
    assert outbox == []


async def test_lists_the_bills_due_in_the_window(
    outbox: list[dict[str, Any]],
    job_session: None,
    a_bill: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_DAYS", 7)
    await jobs.finance_bill_due_email_job()

    assert len(outbox) == 1
    mail = outbox[0]
    assert mail["to"] == "leonard@example.com"
    assert mail["subject"] == "1 bill due in the next 7 days"
    assert "Rent" in mail["text"] and "$1,500.00" in mail["text"]


async def test_a_window_that_ends_before_the_bill_sends_nothing(
    outbox: list[dict[str, Any]],
    job_session: None,
    a_bill: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rent is three days out; a one-day window has nothing to say."""
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_DAYS", 1)
    await jobs.finance_bill_due_email_job()
    assert outbox == []


async def test_a_provider_failure_never_escapes_the_job(
    job_session: None,
    a_bill: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduler must not see an exception: a failed mail is logged
    and the run ends."""
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")

    async def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("Resend is down.")

    monkeypatch.setattr("app.services.comms.email.send_email_simple", _boom)
    await jobs.finance_bill_due_email_job()


def test_the_scheduler_registers_it_once_a_day() -> None:
    from app.components.scheduler import main

    source = main.__file__
    assert source is not None
    text = open(source).read()
    assert 'id="finance_bill_due_email"' in text
    assert "finance_bill_due_email_job," in text


async def test_the_budget_and_goals_are_not_bills(
    outbox: list[dict[str, Any]],
    job_session: None,
    a_bill: None,
    async_db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forecast walks budget drawdowns and goal contributions through
    the same window. Neither is something anyone can be reminded to pay,
    so neither belongs in the mail or its total."""
    finance = FinanceService(async_db_session)
    groceries = await finance.get_or_create_category_from_hint("Food:Groceries")
    await finance.upsert_budget_line(
        owner_user_id=None,
        period_month=None,
        category_id=groceries.id,
        payee_key=None,
        payee_label=None,
        allocated_amount=40_000,
    )
    goal = await finance.create_virtual_goal(
        owner_user_id=None, name="Emergency Fund", target_amount=500_000
    )
    assert goal.id is not None
    await async_db_session.commit()

    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_DAYS", 40)
    await jobs.finance_bill_due_email_job()

    body = outbox[0]["text"]
    assert "Rent" in body
    assert "Food:Groceries" not in body and "Emergency Fund" not in body


async def test_one_address_gets_one_mail(
    outbox: list[dict[str, Any]],
    job_session: None,
    a_bill: None,
    async_db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second owner's bills join the same mail rather than arriving as
    an indistinguishable second copy at the same inbox."""
    finance = FinanceService(async_db_session)
    other = await finance.create_manual_account(
        name="Her Checking", account_type="checking", classification="asset"
    )
    assert other.id is not None
    other.owner_user_id = 7
    async_db_session.add(other)
    await finance.create_recurring_stream(
        owner_user_id=7,
        name="Piano lessons",
        direction="outflow",
        frequency="monthly",
        expected_amount=8_000,
        next_expected_date=date.today() + timedelta(days=2),
        account_id=other.id,
    )
    await async_db_session.commit()

    monkeypatch.setattr(settings, "FINANCE_BILL_EMAIL_TO", "leonard@example.com")
    await jobs.finance_bill_due_email_job()
    assert len(outbox) == 1

