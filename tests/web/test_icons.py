"""The /icons route: stored bytes by key, cached by the browser.

Never a fetch: a key the store does not know is a 404, and the page
never asks for one (it renders the initial instead)."""

import base64

from fastapi.testclient import TestClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.models import FinanceIcon

PNG = base64.b64encode(b"\x89PNG-not-really").decode()


class TestIconRoute:
    async def test_serves_stored_bytes_with_a_day_of_cache(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        async_db_session.add(FinanceIcon(domain="netflix.com", icon_b64=PNG))
        await async_db_session.commit()

        response = client.get("/icons", params={"key": "netflix.com"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == base64.b64decode(PNG)
        assert response.headers["cache-control"] == "public, max-age=86400"
        etag = response.headers["etag"]

        again = client.get(
            "/icons", params={"key": "netflix.com"}, headers={"If-None-Match": etag}
        )
        assert again.status_code == 304

    async def test_unknown_or_negative_key_is_a_404(
        self, client: TestClient, async_db_session: AsyncSession
    ) -> None:
        async_db_session.add(FinanceIcon(domain="nothing.com", icon_b64=None))
        await async_db_session.commit()
        assert client.get("/icons", params={"key": "nothing.com"}).status_code == 404
        assert client.get("/icons", params={"key": "never.com"}).status_code == 404
