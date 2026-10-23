"""What Overseer shows outside dev, kept out of config.py's size budget.

``Settings`` inherits these, so they read as ``settings.OVERSEER_CODE_ENABLED``
and load from ``.env`` like every other setting; each is admins only either
way (``app.core.exposure``).
"""

from typing import Annotated

from pydantic_settings import BaseSettings

from app.core.configurable import Configurable
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
