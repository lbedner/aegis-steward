"""Signing in changes who may read the household's rows, never which rows
they are: steward has one owner, and the rows it kept before auth carry no
owner (``owner_user_id`` NULL, chat and memory under ``"0"``)."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.services.shared.deps import get_admin_actor, get_owner_user_id


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/owner")
    async def owner(owner: int | None = Depends(get_owner_user_id)) -> dict:
        return {"owner": owner}

    @app.get("/actor")
    async def actor(actor=Depends(get_admin_actor)) -> dict:  # noqa: ANN001
        return {"label": actor.label}

    return app


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", ["owner1@test.local"])


def _as_owner() -> dict[str, str]:
    """A seeded owner (``KNOWN_OWNER_IDS``), committed on the app's engine."""
    return {
        "Authorization": f"Bearer {create_access_token({'sub': 'owner1@test.local'})}"
    }


def test_the_signed_in_owner_reads_the_households_rows(auth_on: None) -> None:
    """Owner 1 signed in scopes to ``None``: the NULL-owner rows every read
    already returns, so nothing is migrated and nothing goes missing."""
    response = TestClient(_app()).get("/owner", headers=_as_owner())
    assert response.status_code == 200
    assert response.json() == {"owner": None}


def test_signed_out_there_is_no_owner_scope(auth_on: None) -> None:
    assert TestClient(_app()).get("/owner").status_code == 401


def test_an_operators_change_is_the_signed_in_admins(auth_on: None) -> None:
    response = TestClient(_app()).get("/actor", headers=_as_owner())
    assert response.json() == {"label": "owner1@test.local"}


def test_a_non_admin_makes_no_operators_change(
    auth_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", [])
    response = TestClient(_app()).get("/actor", headers=_as_owner())
    assert response.status_code == 403
