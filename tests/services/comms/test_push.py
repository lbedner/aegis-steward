"""Web push, comms' fourth channel (issue 508).

A browser, or the app added to an iPhone's home screen, subscribes once
and is kept as a ``PushSubscription``; a send reaches every device the
household turned it on for. The signing (VAPID) key is made the first time
it is asked for and kept in the secrets store, because every subscription
is bound to it.
"""

import base64
import json
from typing import Any

from fastapi.testclient import TestClient
import pytest
from pywebpush import WebPushException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.services.comms import push
from app.services.comms.models import PushRegistration, PushSubscription
from tests._secret_settings import use_store

SUBSCRIPTION = {
    "endpoint": "https://web.push.apple.com/QGuQyavXutnMH",
    "keys": {"p256dh": "BPhone-p256dh", "auth": "phone-auth"},
}


def registration(**changes: Any) -> PushRegistration:
    return PushRegistration.model_validate({**SUBSCRIPTION, **changes})


class Gone:
    status_code = 410


@pytest.fixture
def pushed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every push sent; an endpoint containing ``gone`` answers 410. Served
    on https, so the contact is the site whatever ``.env`` says."""
    use_store(monkeypatch)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://steward.example.com")
    sent: list[dict[str, Any]] = []

    async def _webpush(subscription_info: dict[str, Any], **kwargs: Any) -> None:
        if "gone" in subscription_info["endpoint"]:
            raise WebPushException("Push failed: 410 Gone", response=Gone())
        sent.append({"to": subscription_info, **kwargs})

    monkeypatch.setattr("pywebpush.webpush_async", _webpush)
    return sent


async def devices(session: AsyncSession) -> list[PushSubscription]:
    return list((await session.exec(select(PushSubscription))).all())


async def test_the_signing_key_is_made_once_and_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = use_store(monkeypatch)
    first = await push.public_key()
    assert await push.public_key() == first
    assert list(store.values) == [push.VAPID_KEY]
    # What PushManager.subscribe takes: an uncompressed P-256 point.
    point = base64.urlsafe_b64decode(first + "=")
    assert len(point) == 65 and point[0] == 4


async def test_the_key_is_read_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Made at the webserver's start, not in the request that first asks:
    making it is a write, and the Comms page had queried (the bill
    preview) before asking, so the write waited out the busy timeout
    behind the page's own lock. After that, no request touches the store."""
    use_store(monkeypatch)
    from app.components.backend.startup.push_key import startup_hook

    await startup_hook()
    key = await push.public_key()

    async def _locked(name: str) -> None:
        raise AssertionError("a request read the store")

    monkeypatch.setattr(push.secrets, "get", _locked)
    assert await push.public_key() == key


# Each test reads the devices more than once on purpose (keep twice, then
# look); one call never repeats a query.
@pytest.mark.queryspy(threshold=3)
async def test_a_device_is_kept_once(async_db_session: AsyncSession) -> None:
    """A browser re-subscribing (new keys, same endpoint) replaces itself."""
    await push.keep(async_db_session, registration())
    renewed = registration(keys={"p256dh": "new", "auth": "new-auth"})
    await push.keep(async_db_session, renewed)
    (device,) = await devices(async_db_session)
    assert (device.p256dh, device.auth) == ("new", "new-auth")


@pytest.mark.queryspy(threshold=3)
async def test_a_send_reaches_every_device_and_forgets_gone_ones(
    async_db_session: AsyncSession, pushed: list[dict[str, Any]]
) -> None:
    await push.keep(async_db_session, registration())
    await push.keep(
        async_db_session,
        registration(endpoint="https://fcm.googleapis.com/fcm/send/gone"),
    )
    reached = await push.send_push(
        async_db_session, "Rent is due", "$1,500.00 on Friday", url="/bills"
    )

    assert reached == 1
    (sent,) = pushed
    assert sent["to"] == SUBSCRIPTION
    assert json.loads(sent["data"]) == {
        "title": "Rent is due",
        "body": "$1,500.00 on Friday",
        "url": "/bills",
    }
    assert sent["vapid_claims"]["sub"] == push.contact()
    (kept,) = await devices(async_db_session)
    assert kept.endpoint == SUBSCRIPTION["endpoint"]


@pytest.mark.parametrize(
    ("base_url", "admins", "contact"),
    [
        ("https://steward.example.com", [], "https://steward.example.com"),
        # Served over plain http (dev): push services take only https or
        # mailto, so the first admin's address.
        ("http://127.0.0.1:8001", ["sam@example.com"], "mailto:sam@example.com"),
    ],
)
def test_push_services_are_told_who_to_contact(
    monkeypatch: pytest.MonkeyPatch, base_url: str, admins: list[str], contact: str
) -> None:
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", base_url)
    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", admins)
    assert push.contact() == contact


def test_with_no_one_to_contact_push_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://127.0.0.1:8001")
    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", [])
    with pytest.raises(push.PushConfigurationError, match="ADMIN_USER_EMAILS"):
        push.contact()


async def test_no_device_sends_nothing(
    async_db_session: AsyncSession, pushed: list[dict[str, Any]]
) -> None:
    assert await push.send_push(async_db_session, "Hi", "there") == 0
    assert pushed == []


async def test_the_app_registers_a_device(
    async_client_with_db: TestClient, async_db_session: AsyncSession
) -> None:
    """What the page posts after PushManager.subscribe; the native shells
    register through the same endpoint."""
    response = async_client_with_db.post(
        "/api/v1/comms/push/subscriptions", json=SUBSCRIPTION
    )
    assert response.status_code == 204
    (device,) = await devices(async_db_session)
    assert device.endpoint == SUBSCRIPTION["endpoint"]


@pytest.mark.parametrize(
    "refused",
    [
        {"endpoint": SUBSCRIPTION["endpoint"]},
        # The server POSTs to the endpoint: never one inside the network.
        {**SUBSCRIPTION, "endpoint": "https://169.254.169.254/latest"},
        {**SUBSCRIPTION, "endpoint": "https://push.apple.com.evil.example/1"},
        {**SUBSCRIPTION, "endpoint": "http://web.push.apple.com/1"},
    ],
)
def test_only_a_browser_push_service_is_registered(
    async_client_with_db: TestClient, refused: dict[str, Any]
) -> None:
    response = async_client_with_db.post(
        "/api/v1/comms/push/subscriptions", json=refused
    )
    assert response.status_code == 422


def test_the_service_worker_is_served_for_the_whole_site(client: TestClient) -> None:
    """At the root, so its scope is every page."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "showNotification" in response.text
