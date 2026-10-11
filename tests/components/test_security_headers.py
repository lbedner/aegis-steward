"""Every response says: do not guess my type, do not frame me elsewhere,
do not hand my URL to another site."""

from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
import pytest

from app.components.backend.middleware.security_headers import (
    SecurityHeadersMiddleware,
)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.get("/page")
    async def page() -> dict[str, str]:
        return {"page": "overview"}

    @app.get("/own")
    async def own() -> Response:
        return Response(b"x", headers={"X-Frame-Options": "DENY"})

    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def test_every_response_carries_the_headers(client: TestClient) -> None:
    headers = client.get("/page").headers

    assert headers["x-content-type-options"] == "nosniff"
    # Same origin, not DENY: the document dialog embeds the PDF in an
    # <object>, which X-Frame-Options governs like a frame.
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["referrer-policy"] == "same-origin"


def test_a_route_that_sets_its_own_keeps_it(client: TestClient) -> None:
    assert client.get("/own").headers["x-frame-options"] == "DENY"
