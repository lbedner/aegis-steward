"""Locale registry and message resolution."""

import os

from .locales.en import MESSAGES as _EN_MESSAGES

_current_locale: str = "en"
_messages: dict[str, dict[str, str]] = {"en": _EN_MESSAGES}


def set_locale(locale: str) -> None:
    """Set the active locale and eagerly load its messages."""
    global _current_locale
    normalized = _normalize_locale(locale)
    _current_locale = normalized
    _ensure_loaded(normalized)


def get_locale() -> str:
    """Get the active locale code."""
    return _current_locale


def detect_locale() -> str:
    """Detect locale from environment.

    Priority: AEGIS_STEWARD_LANG env var -> system locale -> 'en'
    """
    env_lang = os.environ.get("AEGIS_STEWARD_LANG")
    if env_lang:
        return _normalize_locale(env_lang)

    import locale as locale_mod

    try:
        system_locale, _ = locale_mod.getlocale()
    except Exception:
        system_locale = None

    if system_locale:
        return _normalize_locale(system_locale)

    return "en"


def _normalize_locale(raw: str) -> str:
    """Normalize locale string to a supported code.

    Maps zh_CN, zh-Hans, zh -> 'zh' (Simplified)
    Maps zh_TW, zh_HK, zh-Hant -> 'zh_Hant' (Traditional)
    Maps en_US, en-GB, en -> 'en'
    Unsupported locales fall back to 'en'
    """
    normalized = raw.lower().replace("-", "_").split(".")[0].split("@")[0]
    from .locales import AVAILABLE_LOCALES

    # Traditional Chinese variants
    if normalized in ("zh_tw", "zh_hk", "zh_hant", "zh_mo"):
        return "zh_Hant"

    code = normalized.split("_")[0]
    if code in AVAILABLE_LOCALES:
        return code
    return "en"


def _ensure_loaded(locale: str) -> None:
    """Load a locale's messages if not already cached."""
    if locale in _messages:
        return

    if locale == "zh":
        from .locales.zh import MESSAGES

        _messages["zh"] = MESSAGES
    elif locale == "de":
        from .locales.de import MESSAGES

        _messages["de"] = MESSAGES
    elif locale == "es":
        from .locales.es import MESSAGES

        _messages["es"] = MESSAGES
    elif locale == "fr":
        from .locales.fr import MESSAGES

        _messages["fr"] = MESSAGES
    elif locale == "ja":
        from .locales.ja import MESSAGES

        _messages["ja"] = MESSAGES
    elif locale == "ko":
        from .locales.ko import MESSAGES

        _messages["ko"] = MESSAGES
    elif locale == "ru":
        from .locales.ru import MESSAGES

        _messages["ru"] = MESSAGES
    elif locale == "zh_Hant":
        from .locales.zh_hant import MESSAGES

        _messages["zh_Hant"] = MESSAGES


def translate(key: str, **kwargs: object) -> str:
    """Look up a message key and interpolate.

    Fallback chain: current locale -> English -> raw key.
    """
    msg = _messages.get(_current_locale, {}).get(key)
    if msg is None:
        msg = _messages["en"].get(key)
    if msg is None:
        return key

    if kwargs:
        try:
            return msg.format(**kwargs)
        except (KeyError, IndexError):
            return msg
    return msg


# Auto-detect locale from env var at import time so lazy_t()
# resolves correctly during typer command tree construction.
_detected = detect_locale()
if _detected != "en":
    set_locale(_detected)
