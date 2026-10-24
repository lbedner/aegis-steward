"""Settings Overseer > Settings saves, kept out of config.py's size budget:
what it shows outside dev, and the server and loop the webserver starts on.

``Settings`` inherits these, so they read as ``settings.OVERSEER_CODE_ENABLED``
and load from ``.env`` like every other setting; the first two are admins
only either way (``app.core.exposure``).
"""

from typing import Annotated, Literal

from pydantic_settings import BaseSettings

from app.core.configurable import Configurable, asgi_engines, event_loops
from app.core.constants import ComponentName


class OverseerSettings(BaseSettings):
    OVERSEER_CODE_ENABLED: Annotated[
        bool,
        Configurable(
            ComponentName.WEB_FRONTEND,
            "Show the project's source on Overseer > Code outside dev (admins only)",
        ),
    ] = False
    OVERSEER_DATABASE_ROWS_ENABLED: Annotated[
        bool,
        Configurable(
            ComponentName.WEB_FRONTEND,
            "Show the tables' rows on Overseer > Database outside dev (admins only)",
        ),
    ] = False

    # ASGI server: `make serve ENGINE=granian` for one run, a deployment's env
    # file for good, or saved in the Overseer, which the entrypoint reads first.
    WEBSERVER_ENGINE: Annotated[
        Literal["uvicorn", "granian"],
        Configurable(
            ComponentName.BACKEND,
            "ASGI server the webserver runs on",
            choices=asgi_engines,
        ),
    ] = "uvicorn"

    # Event loop, a separate axis from the engine. "auto" resolves to
    # uvloop when installed; it never resolves to rloop on its own.
    WEBSERVER_LOOP: Annotated[
        Literal["auto", "asyncio", "uvloop", "rloop", "zuvloop"],
        Configurable(
            ComponentName.BACKEND,
            "Event loop the webserver runs on",
            choices=event_loops,
        ),
    ] = "auto"
