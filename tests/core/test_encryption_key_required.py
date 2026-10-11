"""At-rest encryption never falls back to SECRET_KEY outside dev.

The fallback is how stored credentials ended up sealed with the public
template placeholder: SECRET_KEY was unset, so it was the value anyone
reading the template knows. Outside dev the app refuses to boot without
ENCRYPTION_KEY.
"""

from collections.abc import Iterator

import pytest

from app.core import encryption
from app.core.config import Settings, settings

_REAL_SECRET = "not-the-placeholder-" + "x" * 48


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> Settings:
    """Settings from these values alone: no .env, no ambient key."""
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_outside_dev_no_encryption_key_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        _settings(monkeypatch, APP_ENV="prod", SECRET_KEY=_REAL_SECRET)


def test_outside_dev_with_the_key_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = _settings(
        monkeypatch, APP_ENV="prod", SECRET_KEY=_REAL_SECRET, ENCRYPTION_KEY="k"
    )
    assert loaded.ENCRYPTION_KEY == "k"


def test_dev_without_the_key_still_boots(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, APP_ENV="dev").ENCRYPTION_KEY is None


def test_every_dev_env_name_boots_without_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``settings.is_dev`` is the one dev rule, so "local" is dev too."""
    loaded = _settings(monkeypatch, APP_ENV="local", SECRET_KEY=_REAL_SECRET)
    assert loaded.ENCRYPTION_KEY is None


def _as(monkeypatch: pytest.MonkeyPatch, env: str, key: str | None) -> None:
    monkeypatch.setattr(settings, "APP_ENV", env)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key)
    encryption._reset_cache()


@pytest.fixture(autouse=True)
def _fresh_cipher() -> Iterator[None]:
    yield
    encryption._reset_cache()


def test_dev_falls_back_to_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _as(monkeypatch, "dev", None)
    sealed = encryption.encrypt_secret("hunter2")
    assert (
        encryption.decrypt_with(sealed, key_material=settings.SECRET_KEY.encode())
        == "hunter2"
    )


def test_with_the_key_set_it_is_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _as(monkeypatch, "prod", "the-real-key")
    sealed = encryption.encrypt_secret("hunter2")
    assert encryption.decrypt_secret(sealed) == "hunter2"
    assert encryption.decrypt_with(sealed, key_material=b"the-real-key") == "hunter2"
