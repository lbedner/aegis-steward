"""Web push: a notification on every device that turned it on.

A browser, or the app added to an iPhone's home screen, subscribes with
the public half of the VAPID key (``public_key``) and the page registers
what it got (``keep``). ``send_push`` signs with the private half and
posts to each device's push service; a device that is gone (404, 410)
is forgotten. The key is made the first time it is asked for and kept in
the secrets store: every subscription is bound to it, so it never
changes on its own.
"""

import asyncio
import json

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid, b64urlencode
from sqlalchemy import delete
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import secrets
from app.core.config import settings
from app.core.log import logger
from app.core.secrets import Secret

from .models import PushRegistration, PushSubscription
from .queries import push_device, push_devices

VAPID_KEY = "WEB_PUSH_VAPID_KEY"
# A push service's answer for a subscription that no longer exists.
GONE = (404, 410)

# What this module reads (``app.core.secrets``).
SECRETS = (Secret(VAPID_KEY, owner="Web push", label="Signing (VAPID) key, made here"),)


class PushConfigurationError(Exception):
    """No key, and nowhere to keep a new one."""


async def _private_key() -> str:
    if key := await secrets.get(VAPID_KEY):
        return key
    if not secrets.writable():
        raise PushConfigurationError(f"Set {VAPID_KEY} in .env.")
    vapid = Vapid()
    vapid.generate_keys()
    scalar = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    key = b64urlencode(scalar)
    await secrets.put(VAPID_KEY, key, actor="web push")
    return key


async def public_key() -> str:
    """What ``PushManager.subscribe`` takes as ``applicationServerKey``."""
    vapid = Vapid.from_string(private_key=await _private_key())
    point = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64urlencode(point)


async def keep(session: AsyncSession, registration: PushRegistration) -> None:
    """Keep a device, once: a renewed subscription replaces its keys."""
    device = await push_device(session, registration.endpoint) or PushSubscription(
        endpoint=registration.endpoint, p256dh="", auth=""
    )
    device.p256dh = registration.keys.p256dh
    device.auth = registration.keys.auth
    session.add(device)
    await session.commit()


async def send_push(
    session: AsyncSession, title: str, body: str, url: str = "/"
) -> int:
    """Notify every device; how many it reached. Tapping it opens ``url``."""
    devices = await push_devices(session)
    if not devices:
        return 0
    # Imported here: it brings aiohttp and requests, 30 MB the rest of
    # comms never needs.
    from pywebpush import WebPushException, webpush_async

    key = await _private_key()
    data = json.dumps({"title": title, "body": body, "url": url})

    async def _one(device: PushSubscription) -> str:
        try:
            await webpush_async(
                {
                    "endpoint": device.endpoint,
                    "keys": {"p256dh": device.p256dh, "auth": device.auth},
                },
                data=data,
                vapid_private_key=key,
                vapid_claims={"sub": settings.PUBLIC_BASE_URL},
                ttl=24 * 60 * 60,
                timeout=10,
            )
        except WebPushException as exc:
            if exc.status_code in GONE:
                return "gone"
            logger.warning(
                "Push not delivered", endpoint=device.endpoint, error=str(exc)
            )
            return "failed"
        return "sent"

    outcomes = await asyncio.gather(*(_one(device) for device in devices))
    gone = [d.id for d, outcome in zip(devices, outcomes) if outcome == "gone"]
    if gone:
        await session.execute(
            delete(PushSubscription).where(col(PushSubscription.id).in_(gone))
        )
        await session.commit()
    return outcomes.count("sent")
