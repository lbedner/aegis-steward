"""Patch Click's built-in --help option to use translated help text."""

import click.core

from app.i18n import lazy_t

_original_get_help_option = click.core.Command.get_help_option


def _get_help_option_i18n(self, ctx):  # type: ignore[no-untyped-def]
    """Override to replace English help text with lazy-translated string."""
    opt = _original_get_help_option(self, ctx)
    if opt is not None:
        opt.help = lazy_t("shared.show_help_exit")
    return opt


if not getattr(click.core.Command.get_help_option, "_i18n_patched", False):
    click.core.Command.get_help_option = _get_help_option_i18n  # type: ignore[assignment]
    _get_help_option_i18n._i18n_patched = True  # type: ignore[attr-defined]
