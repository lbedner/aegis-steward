"""The sign-in settings a server deploy relies on (#479)."""

import pytest
from starlette.responses import Response

from app.core.config import Settings, settings
from app.core.security import set_refresh_cookie, set_session_cookie

_PROD = {
    "APP_ENV": "prod",
    "SECRET_KEY": "not-the-placeholder-" + "x" * 48,
    "ENCRYPTION_KEY": "k",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> Settings:
    """Settings from these values alone: no .env, no ambient value."""
    for name in ("AUTH_ENABLED", "REGISTRATION_ENABLED", "ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_outside_dev_sign_in_cannot_be_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="AUTH_ENABLED"):
        _settings(monkeypatch, **_PROD, AUTH_ENABLED="false")


def test_dev_may_switch_sign_in_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _settings(monkeypatch, APP_ENV="local", AUTH_ENABLED="false").AUTH_ENABLED
        is False
    )


def test_signups_are_closed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _settings(monkeypatch, **_PROD)
    assert loaded.AUTH_ENABLED is True
    assert loaded.REGISTRATION_ENABLED is False


def test_the_hosts_default_to_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, **_PROD).ALLOWED_HOSTS == [
        "localhost",
        "127.0.0.1",
        "testserver",
    ]


def test_a_server_answers_on_the_domain_its_deploy_stamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``aegis deploy`` stamps DOMAIN into the server's .env: the name it
    is reached on, without a second setting to keep in step."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    loaded = _settings(monkeypatch, **_PROD, DOMAIN="app.example.com")
    assert "app.example.com" in loaded.ALLOWED_HOSTS
    assert loaded.PUBLIC_BASE_URL == "https://app.example.com"
    named = _settings(
        monkeypatch, **_PROD, DOMAIN="app.example.com", PUBLIC_BASE_URL="https://x.dev"
    )
    assert named.PUBLIC_BASE_URL == "https://x.dev"


def _cookies() -> list[str]:
    response = Response()
    set_session_cookie(response, "token")
    set_refresh_cookie(response, "refresh")
    return response.headers.getlist("set-cookie")


@pytest.mark.parametrize(("env", "secure"), [("prod", True), ("local", False)])
def test_session_cookies_are_secure_outside_dev(
    monkeypatch: pytest.MonkeyPatch, env: str, secure: bool
) -> None:
    """``is_dev`` is the one dev rule, so "local" is dev too."""
    monkeypatch.setattr(settings, "APP_ENV", env)
    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", None)
    for cookie in _cookies():
        assert ("; secure" in cookie.lower()) is secure
        assert "httponly" in cookie.lower()
        assert "samesite" in cookie.lower()
