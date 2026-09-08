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


def await_job(client: TestClient, job_id: str, tries: int = 200) -> dict[str, object]:
    """Poll the API until the job is terminal. The test client runs the app
    loop only while a request is in flight, so a background job advances
    between polls, not while a stream waits on it."""
    for _ in range(tries):
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
    raise AssertionError(f"job {job_id} never finished")
