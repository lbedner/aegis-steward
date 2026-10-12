"""Sign-in attempts are counted in Redis (the shared cache), per address and
per email, so a restart or a second process does not hand out a fresh
allowance, and spreading guesses over addresses does not either."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.cache import CacheService
from app.core.config import settings
from app.integrations.main import create_integrated_app


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_integrated_app()


@pytest.fixture(autouse=True)
def _two_tries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN_MAX", 2)


def _sign_in(app: FastAPI, email: str, address: str) -> int:
    client = TestClient(app, client=(address, 50000))
    return client.post(
        "/api/v1/auth/token", data={"username": email, "password": "wrong-one"}
    ).status_code


def test_one_address_runs_out(app: FastAPI) -> None:
    codes = [_sign_in(app, f"u{n}@example.com", "10.0.0.1") for n in range(3)]
    assert codes == [401, 401, 429]


def test_one_email_runs_out_across_addresses(app: FastAPI) -> None:
    codes = [_sign_in(app, "owner@example.com", f"10.0.1.{n}") for n in range(3)]
    assert codes == [401, 401, 429]


def test_the_page_form_shares_the_count(app: FastAPI) -> None:
    for _ in range(2):
        _sign_in(app, "page@example.com", "10.0.2.1")
    client = TestClient(app, client=("10.0.2.2", 50000), follow_redirects=False)
    response = client.post(
        "/login", data={"email": "page@example.com", "password": "wrong-one"}
    )
    assert response.status_code == 429


async def test_a_count_is_a_window() -> None:
    """The counter the limit reads, on the in-memory backend tests run on
    (Redis is INCR, then EXPIRE on the first hit)."""
    cache = CacheService()
    assert [await cache.hit("k", ttl=60) for _ in range(3)] == [1, 2, 3]
    assert await cache.hit("other", ttl=60) == 1
