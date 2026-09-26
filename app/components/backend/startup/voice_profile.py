"""Put the active voice profile to work at startup (#262).

Her voice is table data, like the active model (``llm_active_model``): the
first boot seeds a profile from ``.env``, and from then on the active row is
applied onto the running settings. A missing table (migrations not yet run)
must not stop the app from booting: ``.env`` is already the right default.
"""

from typing import Any

from app.core.log import logger


async def startup_hook() -> Any:
    from app.core.config import settings
    from app.core.db import get_async_session
    from app.services.ai.deps import ai_service
    from app.services.ai.domains.voice import profiles

    try:
        async with get_async_session() as session:
            active = await profiles.load_active(session, settings, ai_service)
    except Exception:
        logger.exception("Could not load the active voice profile")
        return None
    if active is not None:
        logger.info(
            "Voice profile applied: %s (%s, %s)",
            active.name,
            active.tts_voice,
            active.tts_model,
        )
    return None
