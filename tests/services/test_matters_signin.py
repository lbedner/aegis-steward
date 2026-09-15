"""Sign-ins: a password at rest, and what that does and does not buy.

What it protects is a stolen database file. What it does not is a
stolen machine, because the app holds the key - and the tests say which
is which rather than implying more.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.matters.models import SignIn
from app.services.matters.service import PartyService
from app.services.matters.signins import SignInService


async def _party(db: AsyncSession) -> int:
    party = await PartyService(db).create(name="James Bedner", kind="person")
    await db.commit()
    assert party.id is not None
    return party.id


class TestASignIn:
    @pytest.mark.asyncio
    async def test_the_password_is_not_in_the_row(
        self, async_db_session: AsyncSession
    ) -> None:
        """A dump of the table is not a list of logins."""
        party_id = await _party(async_db_session)
        service = SignInService(async_db_session)

        sign_in = await service.add(
            party_id=party_id,
            label="IBEW pension portal",
            url="pensionportal.example.com",
            username="jbedner",
            secret="correct horse battery staple",
        )
        await async_db_session.commit()

        stored = await async_db_session.get(SignIn, sign_in.id)
        assert stored is not None
        assert stored.secret_encrypted is not None
        assert "correct horse" not in stored.secret_encrypted
        assert await service.reveal(sign_in.id) == "correct horse battery staple"
        # A bare host is the usual paste; typing the scheme is the kind
        # of thing that makes somebody not bother.
        assert stored.url == "https://pensionportal.example.com"

    @pytest.mark.asyncio
    async def test_ciphertext_moved_between_rows_does_not_decrypt(
        self, async_db_session: AsyncSession
    ) -> None:
        """The secret is bound to its own row, so swapping ciphertext
        between them fails rather than handing back the wrong password."""
        from app.core.encryption import decrypt_secret

        party_id = await _party(async_db_session)
        service = SignInService(async_db_session)
        first = await service.add(
            party_id=party_id, label="One", secret="first-password"
        )
        second = await service.add(
            party_id=party_id, label="Two", secret="second-password"
        )
        await async_db_session.commit()

        moved = first.secret_encrypted
        assert moved is not None
        with pytest.raises(Exception):
            decrypt_secret(moved, context=f"sign_in:{second.id}:secret")

    @pytest.mark.asyncio
    async def test_editing_the_username_leaves_the_password_alone(
        self, async_db_session: AsyncSession
    ) -> None:
        """A blank password box cannot mean "clear it": the form never
        showed the old one to put back."""
        party_id = await _party(async_db_session)
        service = SignInService(async_db_session)
        sign_in = await service.add(
            party_id=party_id, label="Portal", username="old", secret="keep-me"
        )
        await async_db_session.commit()

        await service.change(sign_in.id, {"username": "new"}, secret=None)
        await async_db_session.commit()

        assert await service.reveal(sign_in.id) == "keep-me"
        changed = await service.get(sign_in.id)
        assert changed is not None
        assert changed.username == "new"

    @pytest.mark.asyncio
    async def test_a_sign_in_needs_a_name(
        self, async_db_session: AsyncSession
    ) -> None:
        party_id = await _party(async_db_session)
        with pytest.raises(ValueError, match="name"):
            await SignInService(async_db_session).add(party_id=party_id, label="  ")
