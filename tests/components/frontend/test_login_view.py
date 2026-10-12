"""The dashboard's sign-in offers signup only while signups are open (#479):
steward has one owner, so by default it offers none."""

from typing import Any

import pytest

from app.components.frontend.auth.login_view import LoginView
from app.core.config import settings
from tests.components.frontend._fakes import FakePage
from tests.components.frontend._tree import walk


def _offers_signup(view: LoginView) -> bool:
    buttons: list[Any] = [n for n in walk(view) if hasattr(n, "text")]
    return any(getattr(b, "text", None) == "Create an account" for b in buttons)


@pytest.mark.parametrize("open_", [True, False])
def test_signup_is_offered_only_while_open(
    monkeypatch: pytest.MonkeyPatch, open_: bool
) -> None:
    monkeypatch.setattr(settings, "REGISTRATION_ENABLED", open_)
    view = LoginView(page=FakePage(), route="/login")  # type: ignore[arg-type]
    assert _offers_signup(view) is open_
