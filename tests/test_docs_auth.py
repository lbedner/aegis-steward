"""Tests for the docs/redoc/openapi.json HTTP Basic auth gate.

The gate lives in ``app.integrations.main.require_docs_auth`` and reads
``settings.DOCS_USERNAME``/``DOCS_PASSWORD``/``APP_ENV`` at request time,
so each test can mutate ``settings`` with ``monkeypatch`` without
rebuilding the app.
"""

from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest

from app.core.config import settings
from app.integrations.main import create_integrated_app

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """Fresh client with creds cleared; tests opt into setting them."""
    monkeypatch.setattr(settings, "DOCS_USERNAME", "")
    monkeypatch.setattr(settings, "DOCS_PASSWORD", "")
    app = create_integrated_app()
    with TestClient(app) as test_client:
        yield test_client


def test_dev_unset_creds_opens_docs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APP_ENV=dev with no creds: docs endpoints serve without auth."""
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    for path in DOCS_PATHS:
        response = client.get(path)
        assert response.status_code == 200, path


def test_prod_unset_creds_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APP_ENV != dev with no creds: docs endpoints fail closed with 404."""
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    for path in DOCS_PATHS:
        response = client.get(path)
        assert response.status_code == 404, path


def test_creds_set_no_auth_header_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "DOCS_USERNAME", "admin")
    monkeypatch.setattr(settings, "DOCS_PASSWORD", "secret")
    for path in DOCS_PATHS:
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.headers.get("WWW-Authenticate") == "Basic", path


def test_creds_set_wrong_creds_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "DOCS_USERNAME", "admin")
    monkeypatch.setattr(settings, "DOCS_PASSWORD", "secret")
    for path in DOCS_PATHS:
        response = client.get(path, auth=("admin", "wrong"))
        assert response.status_code == 401, path


def test_creds_set_correct_creds_serves_docs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "prod")
    monkeypatch.setattr(settings, "DOCS_USERNAME", "admin")
    monkeypatch.setattr(settings, "DOCS_PASSWORD", "secret")

    swagger = client.get("/docs", auth=("admin", "secret"))
    assert swagger.status_code == 200
    assert "swagger" in swagger.text.lower()

    redoc = client.get("/redoc", auth=("admin", "secret"))
    assert redoc.status_code == 200
    assert "redoc" in redoc.text.lower()

    openapi = client.get("/openapi.json", auth=("admin", "secret"))
    assert openapi.status_code == 200
    payload = openapi.json()
    assert payload["openapi"].startswith("3.")
    assert "paths" in payload
