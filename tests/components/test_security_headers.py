"""Every response says: do not guess my type, do not frame me elsewhere,
do not hand my URL to another site. Every page adds: run only my scripts."""

from __future__ import annotations

import re

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import pytest

from app.components.backend.middleware.security_headers import (
    SecurityHeadersMiddleware,
    csp_nonce,
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

    @app.get("/html")
    async def html() -> HTMLResponse:
        return HTMLResponse(f'<script nonce="{csp_nonce()}"></script>')

    @app.get("/own-policy")
    async def own_policy() -> HTMLResponse:
        return HTMLResponse("x", headers={"Content-Security-Policy": "sandbox"})

    @app.get("/dashboard/")
    async def dashboard() -> HTMLResponse:
        return HTMLResponse("<html>flet</html>")

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


def directives(policy: str) -> dict[str, str]:
    """``{directive: its sources}`` of a policy header."""
    split = (part.strip().partition(" ") for part in policy.split(";"))
    return {name: sources for name, _, sources in split}


class TestScriptPolicy:
    def test_a_page_runs_only_this_origin_and_its_own_nonce(
        self, client: TestClient
    ) -> None:
        response = client.get("/html")
        policy = directives(response.headers["content-security-policy"])
        nonce = re.search(r"'nonce-([^']+)'", policy["script-src"])

        assert nonce is not None
        assert f'nonce="{nonce.group(1)}"' in response.text
        assert policy["default-src"] == "'self'"
        # This origin and the nonce; Alpine's standard build needs eval.
        assert policy["script-src"] == f"'self' 'nonce-{nonce.group(1)}' 'unsafe-eval'"
        # The PDF <object>, embedded from this origin only.
        assert policy["object-src"] == "'self'"
        assert policy["frame-ancestors"] == "'self'"
        assert policy["base-uri"] == "'self'"
        assert policy["form-action"] == "'self'"

    def test_every_request_gets_a_fresh_nonce(self, client: TestClient) -> None:
        first = client.get("/html").headers["content-security-policy"]
        assert client.get("/html").headers["content-security-policy"] != first

    def test_only_html_carries_it(self, client: TestClient) -> None:
        assert "content-security-policy" not in client.get("/page").headers

    def test_a_response_with_its_own_policy_keeps_it(self, client: TestClient) -> None:
        """A stored file's sandbox (core/uploads.stored_file) is not loosened."""
        assert client.get("/own-policy").headers["content-security-policy"] == "sandbox"

    def test_the_flet_dashboard_is_left_to_itself(self, client: TestClient) -> None:
        """Flet's page boots from inline script and wasm, and the paste
        capture splices in more; it gets no page policy."""
        assert "content-security-policy" not in client.get("/dashboard/").headers

    def test_outside_a_request_there_is_no_nonce(self) -> None:
        assert csp_nonce() == ""
