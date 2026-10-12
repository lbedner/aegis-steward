"""Nothing but the way in answers a signed-out request (#479).

The gate is one ASGI middleware, so a route, a websocket and a mount (Flet's
``/dashboard``, the static files) all pass the same check. The walk below
asks every route the app serves, so a route added later cannot skip it.
"""

from __future__ import annotations

import re

from fastapi import FastAPI, WebSocket
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
import pytest
from starlette.routing import Mount
from starlette.websockets import WebSocketDisconnect

from app.components.backend.middleware import request_gate
from app.components.backend.startup.finance_webhook_tunnel import PLAID_WEBHOOK_PATH
from app.core.config import settings
from app.core.security import create_access_token
from app.integrations.main import create_integrated_app

PAGE = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_integrated_app()


@pytest.fixture
def signed_out(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    return TestClient(app, follow_redirects=False)


def _concrete(path: str) -> str:
    """``path`` with every parameter filled in."""
    return re.sub(r"{[^}]+}", "1", path)


def _refused(response: object) -> bool:
    status = response.status_code  # type: ignore[attr-defined]
    location = response.headers.get("location", "")  # type: ignore[attr-defined]
    return status == 401 or (status == 303 and "login" in location)


def test_every_route_refuses_a_signed_out_request(
    app: FastAPI, signed_out: TestClient
) -> None:
    let_in = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            path = _concrete(route.path)
            if request_gate.is_public(path):
                continue
            for method in route.methods:
                if not _refused(signed_out.request(method, path)):
                    let_in.append(f"{method} {path}")
        elif isinstance(route, APIWebSocketRoute):
            path = _concrete(route.path)
            with pytest.raises(WebSocketDisconnect):
                with signed_out.websocket_connect(path):
                    let_in.append(f"WS {path}")
        elif isinstance(route, Mount):
            path = _concrete(route.path) + "/"
            if request_gate.is_public(path):
                continue
            if not _refused(signed_out.get(path)):
                let_in.append(f"GET {path}")
    assert let_in == []


def test_the_flet_dashboard_socket_is_refused(signed_out: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect):
        with signed_out.websocket_connect("/dashboard/ws"):
            pass


def test_the_public_list_is_the_way_in_and_nothing_else() -> None:
    assert request_gate.is_public("/login")
    assert request_gate.is_public("/overseer/login")
    assert request_gate.is_public("/api/v1/auth/token")
    assert request_gate.is_public("/api/v1/auth/refresh")
    assert request_gate.is_public("/static/css/app.css")
    assert request_gate.is_public("/health/")
    assert request_gate.is_public(PLAID_WEBHOOK_PATH)
    assert not request_gate.is_public("/health/detailed")
    assert not request_gate.is_public("/api/v1/auth/register")
    assert not request_gate.is_public("/register")
    assert not request_gate.is_public("/dashboard/")
    assert not request_gate.is_public("/staticky")


def test_a_page_signed_out_goes_to_sign_in_and_back(signed_out: TestClient) -> None:
    response = signed_out.get("/accounts", headers=PAGE)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/accounts"


def test_an_overseer_page_goes_to_its_own_sign_in(signed_out: TestClient) -> None:
    response = signed_out.get("/overseer", headers=PAGE)
    assert response.headers["location"] == "/overseer/login?next=/overseer"


def test_htmx_signed_out_is_a_401_auth_js_renews_on(signed_out: TestClient) -> None:
    response = signed_out.get("/accounts", headers={**PAGE, "hx-request": "true"})
    assert response.status_code == 401


def test_the_way_in_answers_signed_out(signed_out: TestClient) -> None:
    assert signed_out.get("/login").status_code == 200
    assert signed_out.get("/health/").status_code == 200


def test_signups_are_closed(signed_out: TestClient) -> None:
    """No open signup: the sign-in page offers none, and the API refuses."""
    assert "/register" not in signed_out.get("/login").text
    response = signed_out.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "long-enough-1"},
    )
    assert response.status_code == 401


def test_a_signed_in_owner_gets_through(signed_out: TestClient) -> None:
    token = create_access_token({"sub": "owner1@test.local"})
    response = signed_out.get(
        "/health/detailed", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_a_stale_token_is_signed_out(signed_out: TestClient) -> None:
    token = create_access_token({"sub": "nobody@test.local"})
    response = signed_out.get(
        "/health/detailed", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


# --- Cross-site writes ------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site", "origin": "http://testserver"},
    ],
)
def test_a_cross_site_write_is_refused(
    signed_out: TestClient, headers: dict[str, str]
) -> None:
    """Before the sign-in check, so the login form is covered too."""
    response = signed_out.post("/login", headers=headers, data={})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "http://testserver"},
        {"sec-fetch-site": "same-origin", "origin": "http://testserver"},
        {},  # not a browser: a bearer client, the CLI
    ],
)
def test_a_same_origin_write_gets_through(
    signed_out: TestClient, headers: dict[str, str]
) -> None:
    response = signed_out.post(
        "/login",
        headers=headers,
        data={"email": "a@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 303


def test_a_cross_site_read_is_untouched(signed_out: TestClient) -> None:
    response = signed_out.get("/login", headers={"sec-fetch-site": "cross-site"})
    assert response.status_code == 200


def test_the_plaid_webhook_is_exempt(signed_out: TestClient) -> None:
    """Plaid posts from its own origin, signed (ES256), never signed in."""
    response = signed_out.post(
        PLAID_WEBHOOK_PATH, headers={"origin": "https://plaid.example"}, json={}
    )
    # The webhook's own answer to an unsigned body, not the gate's.
    assert response.status_code != 403
    assert response.text != "Not signed in"


def test_a_cross_site_socket_is_refused() -> None:
    """Signed in (``AUTH_ENABLED=false`` here), a socket another site opens
    still never connects: its handshake would carry the session cookie."""
    app = FastAPI()

    @app.websocket("/live")
    async def live(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text("hello")
        await socket.close()

    app.add_middleware(request_gate.RequestGate)
    client = TestClient(app)
    with client.websocket_connect("/live") as socket:
        assert socket.receive_text() == "hello"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/live", headers={"origin": "https://evil.example"}
        ):
            pass


# --- Hosts ------------------------------------------------------------------


def test_an_unknown_host_is_refused(signed_out: TestClient) -> None:
    response = signed_out.get("/health/", headers={"host": "evil.example"})
    assert response.status_code == 400
