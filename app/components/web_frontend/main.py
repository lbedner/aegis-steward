"""Web frontend component - Jinja2 + htmx + Alpine.js.

Pages are served at ``/`` by the same webserver that hosts the API and the
Flet dashboard at ``/dashboard``. Route modules import the shared
``templates`` object from here: full-page GETs live in ``routes/pages.py``,
htmx fragments in ``routes/partials/``.
"""

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import FileResponse, Response

from app.components.web_frontend.nav import NAV
from app.core.config import settings

COMPONENT_DIR = Path(__file__).parent
STATIC_DIR = COMPONENT_DIR / "static"
MANIFEST_PATH = STATIC_DIR / "dist" / "manifest.json"

templates = Jinja2Templates(directory=str(COMPONENT_DIR / "templates"))


# ---------------------------------------------------------------------------
# Asset fingerprinting - exposed to templates as a `static()` global.
# The manifest is written by an asset build; it is absent in plain dev,
# where static() falls back to the source path so pages keep rendering with
# no build step.
#
# The manifest is reloaded on demand keyed on the file's mtime: same mtime →
# cached dict, new mtime → re-read. That costs one ``stat()`` per template
# render (microseconds). Without it, a watcher rebuilding ``manifest.json``
# would leave the running webserver serving the fingerprinted URL it cached
# at boot, and the browser would load stale CSS.
# ---------------------------------------------------------------------------

_manifest: dict[str, str] = {}
_manifest_mtime: float = -1.0


def _get_manifest() -> dict[str, str]:
    """Return the current manifest, re-reading it when the file changed.

    Corrupt or missing manifests never raise: callers fall through to
    unhashed source paths.
    """
    global _manifest, _manifest_mtime
    try:
        mtime = MANIFEST_PATH.stat().st_mtime
    except OSError:
        return _manifest  # file missing → keep last known good
    if mtime == _manifest_mtime:
        return _manifest
    try:
        _manifest = json.loads(MANIFEST_PATH.read_text())
        _manifest_mtime = mtime
    except (OSError, json.JSONDecodeError):
        # Mid-write or transient blip - keep the last good copy and try
        # again on the next call. Soft-failing keeps templates rendering.
        pass
    return _manifest


# Prime the cache so the first ``static()`` call doesn't pay the read.
_get_manifest()


def static_url(path: str) -> str:
    """Resolve a logical asset path to its fingerprinted URL.

    Usage in templates: ``{{ static('css/app.css') }}`` - returns
    ``/static/dist/css/app-<hash>.css`` when the manifest was built, else
    ``/static/css/app.css``. Either form serves and renders correctly; the
    fingerprinted one unlocks long-term immutable browser caching.
    """
    built = _get_manifest().get(path)
    if built is not None:
        return f"/static/{built}"
    return f"/static/{path}"


templates.env.globals["static"] = static_url

# Every page's <title> and navbar name the project, so these are globals
# rather than something each route has to remember to pass through.
templates.env.globals["project_name"] = settings.PROJECT_DISPLAY_NAME
templates.env.globals["project_description"] = settings.PROJECT_DESCRIPTION

# Whether the auth service is wired up. Templates gate sign-in affordances
# (and base.html gates the auth JS) on this at render time, so the layout
# stays one file whether or not the project selected auth. AUTH_ENABLED is
# always defined: it is False when the service was not selected.
templates.env.globals["auth_enabled"] = settings.AUTH_ENABLED
# The sidebar loops this; see nav.py.
templates.env.globals["nav"] = NAV
templates.env.globals["registration_enabled"] = settings.REGISTRATION_ENABLED


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

# Fingerprinted filenames an asset build emits: ``foo-<8hex>.css``, nested
# paths included. These are content-addressed - the URL changes whenever the
# file does - so they are safe to cache for a year. Everything else gets the
# conservative one-hour policy.
_FINGERPRINT_SUFFIX = re.compile(r"-[0-9a-f]{8}\.[a-z0-9]+$")


class CachedStaticFiles(StaticFiles):
    """StaticFiles with a two-tier browser Cache-Control policy.

    - Fingerprinted assets (e.g. ``/static/dist/css/app-a1b2c3d4.css``):
      ``max-age=31536000, immutable`` - cached for a year, never
      revalidated. Safe because the URL changes when the content does.
    - Everything else under ``/static/``: ``max-age=3600`` - one hour
      fresh, then a conditional GET against the ETag and Last-Modified
      Starlette already emits returns 304.

    Worst-case staleness for the non-fingerprinted tier is one hour after a
    deploy. For fingerprinted assets it is zero, because the referring HTML
    points at the new hash the moment it deploys.
    """

    def __init__(
        self,
        *args: Any,
        max_age: int = 3600,
        immutable_max_age: int = 31_536_000,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._max_age = max_age
        self._immutable_max_age = immutable_max_age

    def file_response(self, *args: Any, **kwargs: Any) -> FileResponse:
        response = super().file_response(*args, **kwargs)
        served_path = response.path if hasattr(response, "path") else ""
        if _FINGERPRINT_SUFFIX.search(str(served_path)):
            response.headers["Cache-Control"] = (
                f"public, max-age={self._immutable_max_age}, immutable"
            )
        else:
            response.headers["Cache-Control"] = f"public, max-age={self._max_age}"
        return response


def _strftime(value: datetime | str, fmt: str = "%m/%d") -> str:
    """Jinja2 filter: format a datetime or an ISO date string."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime(fmt)


templates.env.filters["strftime"] = _strftime


# ---------------------------------------------------------------------------
# Rendering - one route, two render paths.
# ---------------------------------------------------------------------------

SHELL_LAYOUT = "layouts/app_shell.html"
FRAGMENT_LAYOUT = "layouts/fragment.html"


def wants_fragment(request: Request) -> bool:
    """True when htmx will swap the response into ``#app-content``.

    A boosted request (``hx-boost``) replaces the whole body, so it still
    needs the shell; history restores never reach here because the htmx
    config turns them into full page loads.
    """
    headers = request.headers
    return headers.get("HX-Request") == "true" and headers.get("HX-Boosted") != "true"


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """Render page template ``name`` for either render path.

    The template ends with ``{% extends layout %}``; ``layout`` is set here
    to the app shell on a full load and to the bare fragment when htmx asks,
    so a view has one URL and one template. ``Vary`` tells caches the two
    bodies differ. ``status_code`` is for validation re-renders (422).
    """
    layout = FRAGMENT_LAYOUT if wants_fragment(request) else SHELL_LAYOUT
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


def create_web_frontend_app() -> APIRouter:
    """Create the web frontend router with page and partial routes."""
    router = APIRouter()

    from app.components.web_frontend.routes.finance import router as finance_router
    from app.components.web_frontend.routes.pages import router as pages_router

    router.include_router(pages_router)
    router.include_router(finance_router)

    return router
