"""The Jinja2 environment and the two ways a page is rendered.

Route modules import ``templates``, ``render`` and ``with_toast`` from
here. ``render`` is the one-route-two-paths rule: the same handler serves
a full page inside the app shell and a bare fragment for htmx.
"""

from base64 import b64decode
from hashlib import sha1
import json
from typing import Any, TypeVar
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.responses import Response

from app.components.web_frontend import ranges
from app.components.web_frontend.assets import COMPONENT_DIR, static_url
from app.components.web_frontend.filters import FILTERS, assistant
from app.components.web_frontend.glyphs import (
    account_glyph,
    category_glyph,
    file_badge,
    file_badge_table,
)
from app.components.web_frontend.nav import NAV, section
from app.core import series
from app.core.config import settings
from app.services.finance.constants import account_sections
from app.services.system import topology, ui_runtime

templates = Jinja2Templates(directory=str(COMPONENT_DIR / "templates"))

templates.env.globals["static"] = static_url


def voice() -> dict[str, object]:
    """How Illiana is heard, for voice.js (core/voice_settings.py). Read
    per render, never bound at import, so an .env change needs no code."""
    return {
        "reply": settings.VOICE_REPLY,
        "sound": settings.VOICE_WORKING_SOUND,
        "idle": settings.VOICE_LIVE_IDLE_SECONDS,
        # A live engine is set: talking is the phone, not push-to-talk (#414).
        "live": bool(settings.VOICE_LIVE_ENGINE),
    }


templates.env.globals["voice"] = voice
# Every page's <title> and sidebar name the project, so these are globals
# rather than something each route has to remember to pass through.
templates.env.globals["project_name"] = settings.PROJECT_DISPLAY_NAME
# What an account's things are CALLED, so a card title and the menu item
# that edits it cannot drift apart (services.finance.constants).
from app.services.finance.constants import ACCOUNT_THINGS  # noqa: E402

templates.env.globals["named"] = ACCOUNT_THINGS
templates.env.globals["project_description"] = settings.PROJECT_DESCRIPTION
# Whether the auth service is wired up. Templates gate sign-in affordances
# on this at render time. AUTH_ENABLED is False when the service was not
# selected.
templates.env.globals["auth_enabled"] = settings.AUTH_ENABLED


def registration_enabled() -> bool:
    """Whether signups are open, read as a page renders: a value saved in
    the Overseer applies once the process has booted, after import."""
    return settings.REGISTRATION_ENABLED


templates.env.globals["registration_enabled"] = registration_enabled
templates.env.globals["email_flows_enabled"] = (
    settings.AUTH_ENABLED and settings.AUTH_LEVEL != "basic"
)
# A container's live figures by name (CPU, Memory...), as Flet and the
# charts name them.
templates.env.globals["figure_names"] = ui_runtime.FIGURES
# What each map node is (a store, the queue, an outside provider), by name.
templates.env.globals["map_roles"] = topology.Role
# Every sparkline's box, the one ``series.sparkline`` draws its points in.
templates.env.globals["spark_box"] = f"0 0 {series.SPARK_WIDTH} {series.SPARK_HEIGHT}"
# The appearance choices, listed once. The sidebar's picker renders the
# ones with a legend; <html> carries all of them for static/js/theme.js,
# which takes the first value of each as the default. The DaisyUI themes
# themselves are generated in tailwind.config.js (tests/web/test_theme.py
# holds the two to the same list).
APPEARANCE: dict[str, tuple[str | None, list[tuple[str, str]]]] = {
    "theme": ("Theme", [("aegis", "Aegis"), ("steward", "Steward")]),
    "mode": ("Mode", [("dark", "Dark"), ("light", "Light"), ("system", "System")]),
    "finish": ("Finish", [("matte", "Matte"), ("lustre", "Lustre")]),
    "assistant": ("Assistant button", [("show", "Show"), ("hide", "Hide")]),
    # railed from its own button, not the picker
    "sidebar": (None, [("wide", "Wide"), ("rail", "Rail")]),
}
templates.env.globals["appearance"] = APPEARANCE
templates.env.globals["appearance_choices"] = {
    key: [value for value, _ in choices] for key, (_, choices) in APPEARANCE.items()
}
# The sidebar loops this; see nav.py.
templates.env.globals["nav"] = NAV
templates.env.globals["category_glyph"] = category_glyph
templates.env.globals["account_glyph"] = account_glyph
templates.env.globals["file_badge"] = file_badge
templates.env.globals["file_badge_table"] = file_badge_table

# The matters vocabulary: every string those pages say comes from one
# module, so a status spelled in a template cannot drift from the same
# status spelled in another.
from app.services.matters.words import (  # noqa: E402
    fact_attributes,
    item_status,
    item_verb,
    moment_tone,
    role_label,
    word,
)

templates.env.globals["word"] = word
templates.env.globals["fact_attributes"] = fact_attributes
templates.env.filters["role_label"] = role_label
templates.env.filters["item_status"] = item_status
templates.env.filters["item_verb"] = item_verb
templates.env.filters["moment_tone"] = moment_tone

from app.services.matters.facts import rate_suffix  # noqa: E402

templates.env.filters["rate_suffix"] = rate_suffix

from app.services.documents.models import kind_label  # noqa: E402

# One label for a document's kind, in the tables and the dialog alike.
templates.env.filters["kind_label"] = kind_label
templates.env.globals["account_sections"] = account_sections
# The chat section's path and the assistant's name, for the shell's drawer
# and the sidebar's trigger, which render on every page.
templates.env.globals["chat_path"] = section("chat").path
# The addresses the chat surface's markup and scripts reach, published
# once rather than rebuilt from the section path. Registered here, with
# the shell that renders on every page; each chat route module adds its
# own (routes/chat.py, chat_models, chat_voices, chat_speech, chat_live).
CHAT_URLS: dict[str, str] = {}
templates.env.globals["chat_urls"] = CHAT_URLS
templates.env.globals["assistant"] = assistant("finance-assistant")
# The "everything" window, so a template can tell a default chip from a
# chosen one without importing the module.
templates.env.globals["all_days"] = ranges.ALL

T = TypeVar("T")


def hx_load(url: str) -> Markup:
    """The attributes for "fetch this as soon as I am on the page, and
    become it": a placeholder that loads its own content (a card's change,
    the composer's voice chip), so the page that holds it needs nothing in
    its own context. ``hx-target="this"`` is stated, never inherited: the
    voice chip's placeholder sits inside the composer form, whose target is
    the thread, and borrowing that replaced the whole conversation with the
    chip (2026-09-25)."""
    return Markup(
        f'hx-get="{escape(url)}" hx-trigger="load" hx-target="this" hx-swap="outerHTML"'
    )


def hx_dialog(url: str, extra: str = "") -> Markup:
    """The attributes for "open this in the one modal" (pattern 4), so no
    template has to remember which element the dialog swaps into.
    ``extra`` rides along for the openers that carry more (an
    ``hx-include`` of the checked rows, a role for a clickable cell).

    ``hx-swap`` is stated, not left to the default, because htmx
    INHERITS it from ancestors: an opener inside a row form that swaps
    itself ``outerHTML`` borrowed that and replaced ``#dialog-body``
    with the dialog's own content. The modal opened once and then every
    later open failed with ``htmx:targetError``, because the element it
    targets no longer existed - "I can't open a new one until I
    refresh". Saying it here costs nothing and cannot be borrowed
    against.
    """
    return _hx_open(url, "#dialog-body", extra)


def hx_drawer(url: str) -> Markup:
    """The attributes for "open this in the side drawer": the panel an item
    is edited in beside the page, without the address bar naming it (the
    list-driven ``drawer_sync`` does that). Same stated swap as
    ``hx_dialog``, for the same reason."""
    return _hx_open(url, "#drawer-body")


def _hx_open(url: str, target: str, extra: str = "") -> Markup:
    return Markup(
        f'hx-get="{escape(url)}" hx-target="{target}" hx-swap="innerHTML" {extra}'
    )


def hx_dialog_post(url: str) -> Markup:
    """The attributes for a dialog's own form: post to ``url`` and swap
    the answer back into the dialog, which is how a 422 re-renders the
    form with its errors (pattern 1 inside pattern 4)."""
    return Markup(f'hx-post="{escape(url)}" hx-target="#dialog-body"')


def hx_filter(url: str, target: str) -> Markup:
    """The attributes for "narrow this list where it stands".

    Like ``hx_replace`` but for a control that must survive its own
    request: it re-requests ``url`` and replaces only ``target`` with the
    same element from the response, so the search box being typed into is
    never swapped. Swapping it takes the caret and the focus with it, and
    the next keystroke lands nowhere. No pushed URL either — a dialog's
    search is not a place you navigate back to.

    ``hx-disinherit`` because htmx hands these attributes down to every
    element inside the form, and an opener placed beside the search box
    borrowed the ``hx-select``: the dialog's answer was narrowed to an
    element it does not contain and the modal opened empty.
    """
    return Markup(
        f'hx-get="{escape(url)}" hx-target="{escape(target)}" '
        f'hx-select="{escape(target)}" hx-swap="outerHTML" '
        'hx-disinherit="hx-get hx-target hx-select hx-swap"'
    )


def hx_page(url: str) -> Markup:
    """The attributes for "go to this view": the section swap the sidebar
    does, and any link that hands the reader from one page to another.

    One recipe, because a link that swaps the content area but forgets to
    push the URL, or lands mid-scroll, is a page that half works.
    """
    return Markup(
        f'hx-get="{escape(url)}" hx-target="#app-content" hx-push-url="true" '
        'hx-swap="innerHTML show:window:top"'
    )


def hx_replace(url: str, target: str, oob: str | None = None) -> Markup:
    """The attributes for "re-request ``url`` and replace ``target`` with
    the same element from the response" (filters, pagers, list links).

    Selecting the element you target needs an outerHTML swap, or every
    request nests a copy; keeping the recipe here means nobody forgets.
    """
    attrs = {
        "hx-get": url,
        "hx-target": target,
        "hx-select": target,
        "hx-swap": "outerHTML",
        "hx-push-url": "true",
    }
    if oob:
        attrs["hx-select-oob"] = oob
    return Markup(" ".join(f'{k}="{escape(v)}"' for k, v in attrs.items()))


def image_response(
    request: Request, icon_b64: str, media_type: str = "image/png"
) -> Response:
    """A stored base64 image served for the browser to cache: a day's
    ``max-age`` and an ETag of its bytes, so a revalidation is a 304 and a
    changed image is fetched fresh. For icon routes (``<img src=...>``)."""
    etag = '"' + sha1(icon_b64.encode(), usedforsecurity=False).hexdigest()[:16] + '"'
    headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(b64decode(icon_b64), media_type=media_type, headers=headers)


async def form_fields(request: Request) -> dict[str, str]:
    """A form post's fields as text, for an editor with more fields than a
    handler's signature should spell out."""
    return {key: str(value) for key, value in (await request.form()).items()}


def form_number(raw: str | None, label: str, kind: type = int) -> Any:
    """An optional number from a form field: blank is None, anything else
    must parse as ``kind`` (``int`` or ``float``) or it is a ``ValueError``
    naming the field, which a handler turns into its error toast."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return kind(text)
    except ValueError:
        noun = "a whole number" if kind is int else "a number"
        raise ValueError(f"{label} must be {noun}.") from None


def one_decimal(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Round the named figures for display; the table macro prints values as-is."""
    return row | {key: f"{float(row.get(key) or 0):.1f}" for key in keys}


def status_cell(label: str, tone: str) -> dict[str, str]:
    """A ``data_table`` status cell (rendered as a ``badge``); ``tone`` is ok,
    warn, error, muted or accent."""
    return {"label": label, "tone": tone}


def columns(
    pairs: tuple[tuple[str, str], ...], *, status: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """A ``ui_*`` module's ``(key, label)`` pairs as ``data_table`` columns,
    the ``status`` keys rendered as badges (their cells are ``status_cell``)."""
    return [
        {"key": key, "label": label} | ({"kind": "status"} if key in status else {})
        for key, label in pairs
    ]


def ranked(rows: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    """Rows for the ``ranked_rows`` macro: each gains the ``ratio`` of its
    ``by`` figure to the largest row's, the width of its bar. A bar is a
    size, so a negative figure (money out) measures by its magnitude."""
    top = max((abs(row[by]) for row in rows), default=0)
    return [row | {"ratio": abs(row[by]) / top if top else 0} for row in rows]


def chart(
    labels: list[str], label: str, values: list[float], money: bool = False
) -> dict[str, Any]:
    """Data for the ``chart_panel`` macro: one labelled series, drawn as
    dollars when ``money``, else as plain numbers (charts.js reads a chart
    that names no format as money)."""
    return {
        "labels": labels,
        "series": [{"label": label, "values": values}],
        "format": "money" if money else "count",
    }


def drawer_state(param: str, url: str | None) -> dict[str, str | None]:
    """What a list hands ``drawer_sync``: the open item's drawer URL (None
    closes it) and the query parameter that holds the item."""
    return {"open_drawer": url, "drawer_param": param}


def with_query(path: str, **params: str | list[str] | None) -> str:
    """``path`` with the given query parameters, leaving out empty ones; a
    list (a multi-select's picks) repeats its key once per value."""
    pairs = [
        (key, value)
        for key, given in params.items()
        for value in (given if isinstance(given, list) else [given])
        if value
    ]
    query = urlencode(pairs)
    return f"{path}?{query}" if query else path


def pager(
    path: str,
    page: int,
    page_size: int,
    total: int,
    *,
    param: str = "page",
    **params: str | list[str] | None,
) -> dict[str, Any] | None:
    """The ``pager`` macro's ``{start, end, total, prev, next}`` for ``page``
    (1-based) of ``total`` items, or None when everything fits on one page.
    ``params`` ride along on the previous/next links (filters, say), the
    empty ones left out (``with_query``); ``param`` names the page number,
    for a second list paged on the same URL."""
    if total <= page_size:
        return None

    def link(number: int) -> str:
        return with_query(path, **params, **{param: str(number)})

    start = (page - 1) * page_size
    return {
        "start": start + 1 if total else 0,
        "end": min(start + page_size, total),
        "total": total,
        "prev": link(page - 1) if page > 1 else None,
        "next": link(page + 1) if start + page_size < total else None,
    }


templates.env.globals["hx_replace"] = hx_replace


def hx_swap(url: str, target: str, verb: str = "get") -> Markup:
    """The attributes for "this block answers with itself" (pattern 2 on
    a block): a verb on ``url`` whose response is the whole ``target``,
    swapped in place. Sign-ins, policies, the budget's suggestions - a
    block every verb re-renders. No pushed URL and no select: the
    response IS the block."""
    return Markup(
        f'hx-{verb}="{escape(url)}" hx-target="{escape(target)}" hx-swap="outerHTML"'
    )


templates.env.globals["hx_swap"] = hx_swap


def hx_lazy(url: str) -> Markup:
    """The attributes for a card that fetches its body once the page is
    up: a placeholder that asks for ``url`` on load and is replaced by
    the answer."""
    return Markup(f'hx-get="{escape(url)}" hx-trigger="load" hx-swap="outerHTML"')


templates.env.globals["hx_lazy"] = hx_lazy
templates.env.globals["hx_page"] = hx_page
templates.env.globals["hx_filter"] = hx_filter
templates.env.globals["hx_load"] = hx_load
templates.env.globals["hx_dialog"] = hx_dialog
templates.env.globals["hx_drawer"] = hx_drawer
templates.env.globals["hx_dialog_post"] = hx_dialog_post
templates.env.filters.update(FILTERS)


SHELL_LAYOUT = "layouts/app_shell.html"
FRAGMENT_LAYOUT = "layouts/fragment.html"


def is_htmx(request: Request) -> bool:
    """A request htmx made. It follows a redirect itself and swaps what it
    gets, so one refused for a dead session gets a plain 401 instead, which
    auth.js answers by renewing the session or signing in whole."""
    return request.headers.get("HX-Request") == "true"


def wants_fragment(request: Request) -> bool:
    """True when htmx will swap the response into ``#app-content``.

    A boosted request (``hx-boost``) replaces the whole body, so it still
    needs the shell; history restores never reach here because the htmx
    config turns them into full page loads.
    """
    return is_htmx(request) and request.headers.get("HX-Boosted") != "true"


def fragment(name: str, **context: Any) -> str:
    """Template ``name`` rendered on its own: an SSE frame or a swapped
    part, not a page."""
    return templates.env.get_template(name).render(**context)


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
    page_layout: str = SHELL_LAYOUT,
) -> Response:
    """Render page template ``name`` for either render path.

    The template ends with ``{% extends layout %}``; ``layout`` is set here
    to the app shell on a full load and to the bare fragment when htmx asks,
    so a view has one URL and one template. ``Vary`` tells caches the two
    bodies differ. ``status_code`` is for validation re-renders (422).
    """
    layout = FRAGMENT_LAYOUT if wants_fragment(request) else page_layout
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context={
            **(context or {}),
            "layout": layout,
            # The sidebar marks the current section on full loads.
            "current_path": request.url.path,
        },
        status_code=status_code,
    )
    response.headers["Vary"] = "HX-Request"
    return response


def dialog(
    request: Request, template: str, /, status_code: int = 200, **context: Any
) -> Response:
    """A dialog's body (pattern 4): a bare fragment, never a layout. The
    partial is swapped into ``#dialog-body``, which opens the modal; a
    422 re-renders the same partial with its errors.

    The first two arguments are positional so a form's own context can
    carry any key it likes, ``name`` and ``template`` included.

    Its address opened DIRECTLY - typed, pasted, a new tab - is not the
    popup asking. That rendered the bare fragment with no page and no CSS
    around it (2026-09-23), so it goes to its section with the dialog
    named, and app.js opens it over the page.
    """
    # The browser's own mark on a top-level visit, rather than "no
    # HX-Request": a script or a test asking for the fragment still
    # gets it, and only a person arriving at the address is moved.
    if request.headers.get("sec-fetch-dest") == "document" and not request.headers.get(
        "HX-Request"
    ):
        from urllib.parse import quote

        from starlette.responses import RedirectResponse

        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        # ponytail: the section root, not the exact page it was opened
        # from; walk up to a matching page route if that ever matters.
        section = "/" + request.url.path.strip("/").split("/")[0]
        return RedirectResponse(f"{section}?dialog={quote(path, safe='/')}", 303)
    response = templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )
    # One URL, two answers: without this the browser stored the popup's
    # fragment and handed it back to the address bar (2026-09-23).
    response.headers["Vary"] = "HX-Request, Sec-Fetch-Dest"
    return response


def trigger(
    response: Response, event: str, detail: Any = None, header: str = "HX-Trigger"
) -> Response:
    """Add an htmx client event to ``response`` via ``HX-Trigger`` (or the
    after-swap/after-settle variants named by ``header``).

    Merges with triggers already on the response; a bare event-name header
    is kept as an event with no detail. The toast region, the dialog and
    any page hook listen for these by name.
    """
    existing = response.headers.get(header)
    triggers: dict[str, Any] = {}
    if existing:
        try:
            triggers = json.loads(existing)
        except json.JSONDecodeError:
            triggers = {name.strip(): None for name in existing.split(",")}
    triggers[event] = detail
    response.headers[header] = json.dumps(triggers)
    return response


def with_toast(response: Response, text: str, tone: str = "ok") -> Response:
    """Attach a toast to any response (pattern 6): a ``toast`` event the
    region in base.html shows."""
    return trigger(response, "toast", {"text": text, "tone": tone})


def toast_response(text: str, tone: str = "ok") -> Response:
    """An action's whole answer when the page stays as it is: a toast.
    An error toast leaves a form holding what was typed."""
    return with_toast(Response(status_code=200), text, tone)


def close_dialog(response: Response) -> Response:
    """Close the one modal from a successful in-dialog action (pattern 4).

    After settle, not before the swap: a plain ``HX-Trigger`` fires first,
    and the swap into ``#dialog-body`` that follows (rows out of band leave
    nothing in it) would re-open the dialog, empty.
    """
    return trigger(response, "dialog:close", header="HX-Trigger-After-Settle")


def navigate(
    response: Response,
    path: str,
    target: str = "#app-content",
    select: str | None = None,
) -> Response:
    """Send the browser to ``path`` the htmx way: a GET with HX-Request
    swapped into ``target`` and pushed to the URL bar (``HX-Location``).
    The usual close of a dialog form that made something new. ``select``
    takes that element out of the answer and swaps it for ``target``
    whole, for a page that answers with more than the target holds.

    Closes the dialog with the plain trigger: htmx follows HX-Location
    instead of swapping this response, so nothing after-settle would ever
    fire, and nothing swaps into the dialog that could re-open it.
    """
    location = {"path": path, "target": target}
    if select:
        location |= {"select": select, "swap": "outerHTML"}
    response.headers["HX-Location"] = json.dumps(location)
    return trigger(response, "dialog:close")


def typed_back(typed: str, name: str) -> bool:
    """A typed confirmation names what it is asked to (case and spacing
    aside): the gate on a permanent delete (``confirm_permanent``)."""
    return " ".join(typed.split()).casefold() == " ".join(name.split()).casefold()


def where_from(request: Request, fallback: str) -> str:
    """The page this dialog was opened from.

    htmx sends ``HX-Current-URL`` with every request, so a dialog already
    knows where the reader was standing and does not need to be told
    twice. Saving used to land you wherever the route happened to name -
    editing a document from the Documents tab dropped you on Overview,
    renaming from anywhere dropped you on the register - and each dialog
    had picked its own answer to a question the browser was already
    answering.

    Only the path and query are taken, never the header whole: it is
    same-origin by construction, and a redirect target read off a header
    is not a thing to hand to a browser unexamined.
    """
    from urllib.parse import urlsplit

    current = request.headers.get("HX-Current-URL") or ""
    if not current:
        return fallback
    split = urlsplit(current)
    path = split.path + (f"?{split.query}" if split.query else "")
    return path if path.startswith("/") else fallback


def go_to(path: str, toast: str, target: str) -> Response:
    """Replace ``target`` with the same element from ``path`` and say what
    happened: the ``hx_replace`` recipe, for a region inside a page (the
    Overseer's main area). Swapping the whole answer in would nest the
    page's shell, sidebar and all, inside the target."""
    response = Response(status_code=200)
    navigate(response, path, target=target, select=target)
    return with_toast(response, toast)


def dialog_done(path: str, toast: str, tone: str = "ok") -> Response:
    """The end of a dialog form that made something: close it, say what
    happened, and send the content area where the result lives."""
    response = Response(status_code=200)
    navigate(response, path)
    return close_dialog(with_toast(response, toast, tone))


def or_404(row: T | None) -> T:
    """The row, or the one 404 every route raises when a URL names a
    thing that is not there."""
    if row is None:
        raise HTTPException(status_code=404)
    return row
