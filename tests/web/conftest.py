"""Fixtures for the web frontend tests.

Every route is rendered two ways: a full page on a cold load and a bare
fragment when htmx asks for it. ``client`` (from the root conftest) covers
the first; ``hx`` covers the second by sending the ``HX-Request`` header
on every request. Both wrap the same ``app`` fixture, so a test module can
swap in a different app for both at once.
"""

from collections.abc import Generator

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def hx(app: FastAPI) -> Generator[TestClient]:
    """A client whose every request carries ``HX-Request: true``."""
    with TestClient(app, headers={"HX-Request": "true"}) as test_client:
        yield test_client
