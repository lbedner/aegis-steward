"""Rendered markup for the templates: model markdown (raw HTML escaped),
highlighted source, and docstrings and health messages with their names
set as code. Registered as filters (``filters.FILTERS``)."""

from functools import cache
import html
import re
from typing import Any

from markupsafe import Markup


# A settled assistant message is markdown from a model. marko renders it
# (GFM: tables, strikethrough, autolinks); raw HTML in the source is
# escaped rather than passed through, so the model can format but never
# inject markup. The mixin is registered LAST so it sits first in the
# renderer's MRO, ahead of GFM's own tag filter.
def _safe_markdown() -> Any:
    from marko import Markdown
    from marko.ext.gfm import GFM
    from marko.helpers import MarkoExtension

    class EscapeHTML:
        def render_html_block(self, element: Any) -> str:
            return html.escape(element.body)

        def render_inline_html(self, element: Any) -> str:
            return html.escape(element.children)

    return Markdown(extensions=[GFM, MarkoExtension(renderer_mixins=[EscapeHTML])])


# The prefix of each numbered line's address: ``L-42`` (``linespans``).
LINE_ANCHOR = "L"


def highlight(
    code: str,
    lang: str | None = None,
    filename: str | None = None,
    lines: bool = False,
    wrap: bool = True,
) -> Markup:
    """Source as HTML, highlighted by Pygments (installed with rich) for
    ``lang`` (``pytb`` a Python traceback), or for ``filename``, or a guess.
    ``lines`` numbers them, each at its own address (``#L-<n>``); without
    ``wrap`` it is the spans alone, for a ``<pre>`` the caller draws. The
    one highlighter."""
    from pygments import highlight as render
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer
    from pygments.util import ClassNotFound

    try:
        if lang:
            lexer = get_lexer_by_name(lang)
        elif filename:
            lexer = get_lexer_for_filename(filename, code)
        else:
            lexer = guess_lexer(code)
    except ClassNotFound:
        lexer = guess_lexer(code)
    formatter = (
        HtmlFormatter(linenos="inline", linespans=LINE_ANCHOR)
        if lines
        else HtmlFormatter(nowrap=not wrap)
    )
    return Markup(render(code, lexer, formatter))


_MARKDOWN = _safe_markdown()


def markdown(text: str | None) -> Markup:
    """Model markdown as HTML, with any raw HTML in it escaped."""
    return Markup(_MARKDOWN.convert(text or ""))


def code(source: str | None, lang: str = "python") -> Markup:
    """Source as highlighted HTML spans (no wrapper, no styles), with the
    classes ``input.css`` colours from the theme tokens (``.hl .k`` and
    friends); ``lang`` is python or json. ``highlight``, unwrapped."""
    return highlight(source or "", lang, wrap=False)


_RST_LITERAL = re.compile(r"``(.+?)``")
# A literal set as code, in the accent: a docstring's, a message's names.
_LITERAL = '<code class="font-mono text-aegis-teal">{}</code>'
# An upper-case name, as settings and secrets are (``STRIPE_SECRET_KEY``).
_NAME = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


def docstring(text: str | None) -> Markup:
    """A docstring as HTML: escaped, its RST ``literals`` set as code.

    Values that are already HTML (``Markup``) pass through as given.
    """
    if isinstance(text, Markup):
        return text
    escaped = html.escape(text or "", quote=False)
    return Markup(_RST_LITERAL.sub(lambda m: _LITERAL.format(m.group(1)), escaped))


def message(text: str | None) -> Markup:
    """A health check's message as HTML: escaped, every setting or secret
    it names (``STRIPE_SECRET_KEY not configured``) set as code, as a
    docstring's literals are. An upper-case word nothing declares (``OK``)
    is left alone. Its own lines stay lines (a driver's error sets the
    statement it ran apart)."""
    known = _declared_names()
    escaped = html.escape(text or "", quote=False)
    named = _NAME.sub(
        lambda m: _LITERAL.format(m[0]) if m[0] in known else m[0], escaped
    )
    return Markup(named.replace("\n", "<br>"))


@cache
def _declared_names() -> frozenset[str]:
    """Every setting and declared secret, by name: code, so read once."""
    from app.core import secrets
    from app.core.config import settings

    names = {entry.name for entry in secrets.declared()}
    return frozenset(names | set(type(settings).model_fields))
