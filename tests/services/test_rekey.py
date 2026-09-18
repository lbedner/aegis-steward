"""Rotating the encryption key without losing what it protected.

Decryption uses exactly one key, so setting ENCRYPTION_KEY for the first
time orphans everything written under the SECRET_KEY fallback - two
sign-in passwords and two live brokerage connections, in the case that
prompted this. You cannot re-encrypt what you cannot read, so the
re-key has to happen while both keys are known.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import encryption
from app.core.config import settings
from app.core.encryption import decrypt_secret, decrypt_with, encrypt_secret


def _as(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    """Run as if ENCRYPTION_KEY were this."""
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", key)
    encryption._reset_cache()


class TestReadingWithAKeyThatIsNotTheCurrentOne:
    def test_it_round_trips_against_explicit_material(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _as(monkeypatch, None)
        old = settings.SECRET_KEY.encode()
        sealed = encrypt_secret("hunter2", context="row:1")

        _as(monkeypatch, "a-new-key-entirely")

        assert decrypt_secret.__name__  # the current key cannot read it
        with pytest.raises(Exception):
            decrypt_secret(sealed, context="row:1")
        assert decrypt_with(sealed, key_material=old, context="row:1") == "hunter2"

    def test_the_context_still_has_to_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _as(monkeypatch, None)
        sealed = encrypt_secret("hunter2", context="row:1")
        with pytest.raises(Exception):
            decrypt_with(
                sealed, key_material=settings.SECRET_KEY.encode(), context="row:2"
            )


class TestEveryColumnIsAccountedFor:
    def test_no_encrypted_column_is_left_out_of_the_pass(self) -> None:
        """A new encrypted column that nobody registered is one the
        re-key silently skips, and its rows are lost on the next
        rotation. The registry is checked against the models."""
        from sqlmodel import SQLModel

        from app.services.system.rekey import SECRETS

        declared = {
            f"{table.name}.{column.name}"
            for table in SQLModel.metadata.tables.values()
            for column in table.columns
            if column.name.endswith("_encrypted")
        }
        registered = {f"{one.table}.{one.column}" for one in SECRETS}
        assert declared - registered == set()


class TestTheRotation:
    @pytest.mark.asyncio
    async def test_what_the_old_key_sealed_the_new_one_opens(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters.service import PartyService
        from app.services.matters.signins import SignInService
        from app.services.system.rekey import rekey_secrets

        _as(monkeypatch, None)
        party = await PartyService(async_db_session).create(
            name="Old Key Bank", kind="organization"
        )
        await SignInService(async_db_session).add(
            party_id=int(party.id), label="Portal", username="me", secret="hunter2"
        )
        await async_db_session.commit()

        old = settings.SECRET_KEY.encode()
        _as(monkeypatch, "a-new-key-entirely")
        counted = await rekey_secrets(async_db_session, old_key_material=old)
        await async_db_session.commit()

        assert counted["sign_in.secret_encrypted"] == 1
        signins = await SignInService(async_db_session).for_party(int(party.id))
        assert (
            await SignInService(async_db_session).reveal(int(signins[0].id))
        ) == "hunter2"

    @pytest.mark.asyncio
    async def test_running_it_twice_changes_nothing_the_second_time(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row already under the new key fails the old-key read and is
        left alone, so a re-run is safe rather than double-sealing it."""
        from app.services.matters.service import PartyService
        from app.services.matters.signins import SignInService
        from app.services.system.rekey import rekey_secrets

        _as(monkeypatch, None)
        party = await PartyService(async_db_session).create(
            name="Twice Bank", kind="organization"
        )
        await SignInService(async_db_session).add(
            party_id=int(party.id), label="Portal", username="me", secret="hunter2"
        )
        await async_db_session.commit()

        old = settings.SECRET_KEY.encode()
        _as(monkeypatch, "a-new-key-entirely")
        await rekey_secrets(async_db_session, old_key_material=old)
        await async_db_session.commit()

        again = await rekey_secrets(async_db_session, old_key_material=old)
        await async_db_session.commit()

        assert again["sign_in.secret_encrypted"] == 0
        signins = await SignInService(async_db_session).for_party(int(party.id))
        assert (
            await SignInService(async_db_session).reveal(int(signins[0].id))
        ) == "hunter2"

    @pytest.mark.asyncio
    async def test_a_row_with_no_secret_is_not_counted(
        self, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.matters.service import PartyService
        from app.services.matters.signins import SignInService
        from app.services.system.rekey import rekey_secrets

        _as(monkeypatch, None)
        party = await PartyService(async_db_session).create(
            name="No Secret Bank", kind="organization"
        )
        await SignInService(async_db_session).add(
            party_id=int(party.id), label="Portal", username="me", secret=""
        )
        await async_db_session.commit()

        counted = await rekey_secrets(
            async_db_session, old_key_material=settings.SECRET_KEY.encode()
        )

        assert counted["sign_in.secret_encrypted"] == 0
