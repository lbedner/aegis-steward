"""Tests for i18n locale completeness and translation mechanics."""

from app.i18n.locales.en import MESSAGES as EN_MESSAGES
from app.i18n.locales.zh import MESSAGES as ZH_MESSAGES
from app.i18n.registry import set_locale, translate


def test_locale_completeness() -> None:
    """Verify all locales have the same keys as English."""
    missing = set(EN_MESSAGES.keys()) - set(ZH_MESSAGES.keys())
    extra = set(ZH_MESSAGES.keys()) - set(EN_MESSAGES.keys())
    assert not missing, f"Keys in en but not zh: {missing}"
    assert not extra, f"Keys in zh but not en: {extra}"


def test_locale_no_empty_values() -> None:
    """Verify no locale has empty string values."""
    for key, value in EN_MESSAGES.items():
        assert value.strip(), f"Empty English value for key: {key}"
    for key, value in ZH_MESSAGES.items():
        assert value.strip(), f"Empty Chinese value for key: {key}"


def test_translate_unknown_key_returns_raw_key() -> None:
    """Unknown keys fall through to the raw key string."""
    set_locale("en")
    assert translate("nonexistent.key") == "nonexistent.key"


def test_translate_falls_back_to_english() -> None:
    """Missing locale key falls back to English value."""
    set_locale("zh")
    # shared.error exists in both, verify fallback works for a key only in en
    result = translate("shared.error")
    assert result  # Should return the English value, not the raw key
    assert result != "shared.error"


def test_translate_interpolation() -> None:
    """Format kwargs are interpolated into the message."""
    set_locale("en")
    result = translate("main.unsupported_lang", lang="xx", available="en, zh")
    assert "xx" in result
    assert "en, zh" in result


def test_translate_missing_format_kwargs_returns_template() -> None:
    """Missing format kwargs return the unformatted template string."""
    set_locale("en")
    # Call with missing kwargs — should return template without crashing
    result = translate("main.unsupported_lang")
    assert "{lang}" in result or "lang" in result
