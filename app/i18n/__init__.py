"""
Internationalization support for the project CLI.

Usage:
    import typer

    from app.cli import theme
    from app.i18n import lazy_t, t

    # Runtime strings (resolved immediately):
    theme.accent(t("health.title"), bold=True)

    # Help text (resolved lazily when displayed):
    app = typer.Typer(help=lazy_t("health.help"))

Note: We use ``t`` instead of the conventional ``_`` because ``_`` is
commonly used as a throwaway variable in tuple unpacking throughout the
codebase (e.g., ``result, _ = func()``), which would shadow the import.
"""

from typing import Any

from .registry import detect_locale, get_locale, set_locale, translate


def t(key: str, **kwargs: object) -> str:
    """Translate a message key with optional interpolation.

    Fallback chain: current locale -> English -> raw key.

    Args:
        key: Dot-separated message key (e.g., "health.title")
        **kwargs: Values for str.format() interpolation

    Returns:
        Translated string
    """
    return translate(key, **kwargs)


class _Lazy:
    """Lazy translation that resolves when accessed as a string.

    Used for typer help= parameters where the value must resolve after
    the locale is set, not at import time. Delegates all string methods
    to the resolved translation via __getattr__.
    """

    __slots__ = ("_key", "_kw")

    def __init__(self, key: str, **kw: object) -> None:
        self._key = key
        self._kw = kw

    def __str__(self) -> str:
        return translate(self._key, **self._kw)

    def __repr__(self) -> str:
        return str(self)

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        return len(str(self))

    def __contains__(self, item: object) -> bool:
        return item in str(self)

    def __add__(self, other: object) -> str:
        return str(self) + str(other)

    def __radd__(self, other: object) -> str:
        return str(other) + str(self)

    def __getitem__(self, key: Any) -> str:
        return str(self)[key]

    def __format__(self, format_spec: str) -> str:
        return format(str(self), format_spec)

    def __getattr__(self, name: str) -> Any:
        return getattr(str(self), name)


def lazy_t(key: str, **kwargs: object) -> _Lazy:
    """Lazy translation for typer help= parameters.

    Returns a lazy object that resolves to the translated string
    when click/typer accesses it for display.

    Args:
        key: Dot-separated message key
        **kwargs: Values for str.format() interpolation

    Returns:
        Lazy translation object
    """
    return _Lazy(key, **kwargs)


__all__ = ["t", "lazy_t", "set_locale", "get_locale", "detect_locale"]
