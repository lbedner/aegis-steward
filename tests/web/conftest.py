"""Fixtures for the web frontend tests.

Every route is rendered two ways: a full page on a cold load and a bare
fragment when htmx asks for it. ``client`` (from the root conftest) covers
the first; ``hx`` covers the second by sending the ``HX-Request`` header
on every request. Both wrap the same ``app`` fixture, so a test module can
swap in a different app for both at once.
"""

from collections.abc import AsyncGenerator, Callable, Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.components.web_frontend.rendering import templates
from app.core.db import get_async_db
from app.integrations.main import create_integrated_app
from app.services.finance.service import FinanceService


@pytest.fixture
def app(async_db_session: AsyncSession) -> Generator[FastAPI]:
    """The real app on the test database.

    Overrides the root ``app`` fixture so ``client`` and ``hx`` both see the
    per-test session: pages call the finance service in-process, so a
    page test seeds through ``finance`` and reads through either client.
    """
    application = create_integrated_app()

    async def _test_db() -> AsyncGenerator[AsyncSession]:
        yield async_db_session

    application.dependency_overrides[get_async_db] = _test_db
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def finance(async_db_session: AsyncSession) -> FinanceService:
    """Seed ledger rows for a page test; commit before requesting the page."""
    return FinanceService(async_db_session)


@pytest.fixture
def hx(app: FastAPI) -> Generator[TestClient]:
    """A client whose every request carries ``HX-Request: true``."""
    with TestClient(app, headers={"HX-Request": "true"}) as test_client:
        yield test_client


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
