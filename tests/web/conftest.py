"""Fixtures for the web frontend tests.

Every route is rendered two ways: a full page on a cold load and a bare
fragment when htmx asks for it. ``client`` (from the root conftest) covers
the first; ``hx`` covers the second by sending the ``HX-Request`` header
on every request. Both wrap the same ``app`` fixture, so a test module can
swap in a different app for both at once.
"""

from collections.abc import AsyncGenerator, Callable, Generator
from dataclasses import dataclass
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.rendering import templates
from app.core.db import get_async_db
from app.integrations.main import create_integrated_app
from app.services.finance.service import FinanceService

_SESSION: dict[str, AsyncSession] = {}


async def _test_db() -> AsyncGenerator[AsyncSession]:
    yield _SESSION["current"]


@pytest.fixture(scope="module")
def app() -> Generator[FastAPI]:
    """The real app, built once per module, on the test database.

    Pages call the finance service in-process, so the app's ``get_async_db``
    is overridden to hand out whichever per-test session ``_bind_session``
    has current. Building the app per test and running its lifespan (the
    startup hooks, the Redis job store's connect attempt) was most of a
    page test's half second; none of it is needed to render a page.
    """
    application = create_integrated_app()
    application.dependency_overrides[get_async_db] = _test_db
    yield application
    application.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _bind_session(async_db_session: AsyncSession) -> Generator[None]:
    _SESSION["current"] = async_db_session
    yield
    _SESSION.pop("current", None)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Plain client, no lifespan: routes render without startup hooks."""
    return TestClient(app)


@pytest.fixture
def live_client(app: FastAPI) -> Generator[TestClient]:
    """A client that holds the app's lifespan (one event loop) open across
    requests, for tests that start a background job and poll it: without
    the lifespan each request gets a fresh loop and the job task dies."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def hx(app: FastAPI) -> TestClient:
    """A client whose every request carries ``HX-Request: true``."""
    return TestClient(app, headers={"HX-Request": "true"})


@pytest.fixture
def finance(async_db_session: AsyncSession) -> FinanceService:
    """Seed ledger rows for a page test; commit before requesting the page."""
    return FinanceService(async_db_session)


@pytest.fixture
def add_template() -> Generator[Callable[[str, str], None]]:
    """Register a throwaway named template for the duration of one test.

    Lets a test exercise the real ``templates`` environment (globals,
    filters, loader) through a probe page without shipping test-only
    files under ``app/``. The original loader is restored afterwards.
    """
    env = templates.env
    original = env.loader
    added: dict[str, str] = {}
    env.loader = ChoiceLoader([DictLoader(added), original])  # type: ignore[list-item]

    def _add(name: str, source: str) -> None:
        added[name] = source
        if env.cache is not None:
            env.cache.clear()

    yield _add
    env.loader = original


@dataclass(frozen=True)
class Ledger:
    """Ids of the seeded ledger (see the ``ledger`` fixture)."""

    checking: int
    savings: int
    card: int


@pytest.fixture
async def ledger(finance: FinanceService, async_db_session: AsyncSession) -> Ledger:
    """The one seeded ledger page tests share.

    Checking $100.00 and Savings $50.00 (assets), Visa -$25.00 (liability).
    This month: two grocery outflows and a fuel outflow (categorised), one
    uncategorised charge, one payroll deposit. Committed, so both clients
    see it. Figures worth knowing: assets $150.00, net worth $125.00,
    spending Food $45.00 + Auto $20.00, income $500.00, outflows $72.00.
    """
    ids: dict[str, int] = {}
    for name, kind, classification, balance in (
        ("Checking", "checking", "asset", 10_000),
        ("Savings", "savings", "asset", 5_000),
        ("Visa", "credit_card", "liability", -2_500),
    ):
        account = await finance.create_manual_account(
            name=name, account_type=kind, classification=classification
        )
        assert account.id is not None
        await finance.update_account_balance(account.id, current_balance=balance)
        ids[name] = account.id
    groceries = await finance.get_or_create_category_from_hint("Food:Groceries")
    fuel = await finance.get_or_create_category_from_hint("Auto:Fuel")
    today = date.today()
    rows = (
        ("Market", -3_000, groceries, ids["Checking"]),
        ("Market", -1_500, groceries, ids["Checking"]),
        ("Gas", -2_000, fuel, ids["Visa"]),
        ("Mystery charge", -700, None, ids["Visa"]),
        ("Payroll", 50_000, None, ids["Checking"]),
    )
    for offset, (name, amount, category, account_id) in enumerate(rows):
        txn = await finance.create_transaction(
            account_id=account_id,
            amount=amount,
            txn_date=today - timedelta(days=offset),
            name=name,
        )
        txn.category_id = category.id if category is not None else None
        async_db_session.add(txn)
    await async_db_session.commit()
    return Ledger(checking=ids["Checking"], savings=ids["Savings"], card=ids["Visa"])


@dataclass(frozen=True)
class Streams:
    """Ids of the seeded recurring streams (see the ``streams`` fixture)."""

    rent: int
    payroll: int
    netflix: int
    water: int


@pytest.fixture
async def streams(
    finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
) -> Streams:
    """Four streams on the ledger, one per state the Bills page shows.

    Rent (bill, $1,500.00 monthly, due in 10 days, hand-entered), Payroll
    (income, $2,500.00 every 2 weeks), Netflix (detected, $15.99, not yet
    confirmed) and Water (bill, $45.00 monthly, due 10 days ago: overdue,
    and with one unclaimed $46.00 "WATER CO" charge to match it to).
    """
    today = date.today()
    ids: dict[str, int] = {}
    for name, direction, frequency, amount, due in (
        ("Rent", "outflow", "monthly", 150_000, today + timedelta(days=10)),
        ("Payroll", "inflow", "biweekly", 250_000, today + timedelta(days=7)),
        ("Netflix", "outflow", "monthly", 1_599, today + timedelta(days=3)),
        ("Water", "outflow", "monthly", 4_500, today - timedelta(days=10)),
    ):
        stream = await finance.create_recurring_stream(
            owner_user_id=None,
            name=name,
            direction=direction,
            frequency=frequency,
            expected_amount=amount,
            next_expected_date=due,
            account_id=ledger.checking,
        )
        assert stream.id is not None
        ids[name] = stream.id
    netflix = await finance.get_recurring(ids["Netflix"], None)
    assert netflix is not None
    netflix.source = "derived"
    netflix.is_user_confirmed = False
    netflix.expected_amount = None
    async_db_session.add(netflix)
    await finance.create_transaction(
        account_id=ledger.checking,
        amount=-4_600,
        txn_date=today - timedelta(days=9),
        name="WATER CO",
    )
    await async_db_session.commit()
    return Streams(
        rent=ids["Rent"],
        payroll=ids["Payroll"],
        netflix=ids["Netflix"],
        water=ids["Water"],
    )


@dataclass(frozen=True)
class Budget:
    """Ids of the seeded budget (see the ``budget`` fixture)."""

    groceries: int  # category id
    fuel: int  # category id
    line: int  # the Food:Groceries limit
    goal: int  # account id of the virtual goal
    envelope: int  # account id


@pytest.fixture
async def budget(
    finance: FinanceService, async_db_session: AsyncSession, streams: Streams
) -> Budget:
    """A budget on top of the ledger and its streams.

    One flexible limit (Food:Groceries, $200.00 a month), one virtual goal
    (Vacation, $1,000.00 target, $250.00 saved so far) and one envelope
    (Kids, $20.00 a month, $50.00 in it).
    """
    groceries = await finance.get_or_create_category_from_hint("Food:Groceries")
    fuel = await finance.get_or_create_category_from_hint("Auto:Fuel")
    line = await finance.upsert_budget_line(
        owner_user_id=None,
        period_month=None,
        category_id=groceries.id,
        payee_key=None,
        payee_label=None,
        allocated_amount=20_000,
    )
    goal = await finance.create_virtual_goal(
        owner_user_id=None, name="Vacation", target_amount=100_000
    )
    assert goal.id is not None
    await finance.contribute_to_goal(goal.id, amount=25_000, owner_user_id=None)
    envelope = await finance.create_envelope(
        owner_user_id=None, name="Kids", monthly_credit=2_000, starting_balance=5_000
    )
    assert envelope.id is not None and groceries.id is not None and fuel.id is not None
    await async_db_session.commit()
    return Budget(
        groceries=groceries.id,
        fuel=fuel.id,
        line=line.id,
        goal=goal.id,
        envelope=envelope.id,
    )


@dataclass(frozen=True)
class Review:
    """Ids of the seeded review queues (see the ``review`` fixture)."""

    change: int  # a single pending categorize
    batch: str  # a two-row batch of payee assignments
    insight: int
    mystery: int  # the uncategorised, payee-less transaction


@pytest.fixture
async def review(
    finance: FinanceService, async_db_session: AsyncSession, ledger: Ledger
) -> Review:
    """Something in every queue: one pending change and a batch of two,
    one new insight, and the ledger's uncategorised "Mystery charge"."""
    from app.services.finance.models import FinanceInsight

    rows, _total = await finance.list_transactions(owner_user_id=None, page_size=50)
    by_name = {t.name: t for t in rows}
    mystery = by_name["Mystery charge"]
    gas = by_name["Gas"]
    groceries = await finance.get_or_create_category_from_hint("Food:Groceries")
    change = await finance.propose_change(
        "transaction.categorize",
        {"transaction_id": mystery.id, "category_id": groceries.id},
        owner_user_id=None,
        proposed_by_agent="steward",
    )
    batch = await finance.propose_many_changes(
        "transaction.assign_payee",
        [
            {"transaction_id": gas.id, "payee": "Shell"},
            {"transaction_id": mystery.id, "payee": "Shell"},
        ],
        owner_user_id=None,
        proposed_by_agent="steward",
    )
    insight = FinanceInsight(
        owner_user_id=0,
        insight_type="price_hike",
        severity="warning",
        title="Netflix went up",
        body="From $15.49 to $20.54.",
        dedup_key="test:price_hike:netflix",
    )
    async_db_session.add(insight)
    await async_db_session.commit()
    assert change.id is not None and insight.id is not None and mystery.id is not None
    assert batch[0].batch_id is not None
    return Review(
        change=change.id,
        batch=batch[0].batch_id,
        insight=insight.id,
        mystery=mystery.id,
    )


def await_job(client: TestClient, job_id: str, tries: int = 200) -> dict[str, object]:
    """Poll the API until the job is terminal. The test client runs the app
    loop only while a request is in flight, so a background job advances
    between polls, not while a stream waits on it."""
    for _ in range(tries):
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
    raise AssertionError(f"job {job_id} never finished")
