"""The app shell's way out: Sign out sits in the sidebar's appearance
panel, where the Overseer's viewer menu keeps its own."""

from fastapi.testclient import TestClient
import pytest

from app.components.web_frontend.rendering import templates
from tests.web.dom import one, select


def test_the_sidebar_signs_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(templates.env.globals, "auth_enabled", True)
    aside = one(client.get("/overview").text, "aside#sidebar")
    assert one(aside, "a[data-sign-out]").get("href") == "/logout"


def test_without_auth_there_is_nothing_to_sign_out_of(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(templates.env.globals, "auth_enabled", False)
    aside = one(client.get("/overview").text, "aside#sidebar")
    assert select(aside, "a[data-sign-out]") == []
