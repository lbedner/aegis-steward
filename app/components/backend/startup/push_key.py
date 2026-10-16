"""Read (or make) the web push key as the webserver starts, so no request
ever opens the secrets store under its own write lock
(``app.services.comms.push``)."""

from app.core.log import logger
from app.services.comms import push


async def startup_hook() -> None:
    try:
        await push.public_key()
    except push.PushConfigurationError as exc:
        logger.info("Web push is off", reason=str(exc))
