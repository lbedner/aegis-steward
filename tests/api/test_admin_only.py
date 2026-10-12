"""What spends money or runs work on demand is the admins' alone (#479):
comms sends, task enqueue, load tests, scheduler run-now, container
restarts. Signed in is not
enough; ``ADMIN_USER_EMAILS`` decides."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.core.security import create_access_token
from app.integrations.main import create_integrated_app

OWNER = "owner1@test.local"

ADMIN_ONLY = [
    ("POST", "/api/v1/comms/email/send"),
    ("POST", "/api/v1/comms/sms/send"),
    ("POST", "/api/v1/comms/call/make"),
    ("POST", "/api/v1/tasks/enqueue"),
    ("POST", "/api/v1/tasks/load-test"),
    ("POST", "/api/v1/scheduler/jobs/some-job/run"),
    ("POST", "/api/v1/runtime/containers/someone-elses-db-1/restart"),
]


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_integrated_app()


@pytest.fixture
def owner(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    token = create_access_token({"sub": OWNER})
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY)
def test_a_signed_in_non_admin_is_refused(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", [])
    assert owner.request(method, path, json={}).status_code == 403


@pytest.mark.parametrize(("method", "path"), ADMIN_ONLY)
def test_an_admin_gets_past_the_check(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    """Past it, the handler refuses the malformed body (422), the unknown
    job (404) or, with no Docker here, the restart (503) itself: nothing is
    sent or run."""
    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", [OWNER])
    assert owner.request(method, path, json=[]).status_code in (404, 422, 503)
