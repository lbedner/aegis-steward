"""``secrets``: rotating the key that protects what is stored at rest.

Its own module because the act is not about the ledger or the agents:
it is about every stored secret at once, and it has to be findable by
somebody who is about to change ``ENCRYPTION_KEY`` and wants to know
what that costs.
"""

from __future__ import annotations

import asyncio

import typer

from app.cli import theme

secrets_app = typer.Typer(help="The key that protects stored secrets")
console = theme.console()


@secrets_app.command("rekey")
def rekey(
    from_secret_key: bool = typer.Option(
        False,
        "--from-secret-key",
        help="Read the old ciphertext with the SECRET_KEY fallback, which is "
        "what every row was sealed with before ENCRYPTION_KEY was set.",
    ),
) -> None:
    """Re-encrypt every stored secret under the key now in force.

    Decryption uses exactly ONE key, so setting ``ENCRYPTION_KEY`` for
    the first time orphans everything written under the ``SECRET_KEY``
    fallback - and you cannot re-encrypt what you cannot read. So: put
    the new key in ``.env``, leave ``SECRET_KEY`` alone, and run this
    BEFORE relying on anything. It reads each row with the old material
    and writes it back with the new.

    Neither key is ever typed here. Both are read from the environment,
    so a key never lands in a shell history.

    Safe to run twice: a row the old key cannot read is already rotated
    and is left exactly as it is.
    """
    from app.core.config import settings
    from app.core.db import get_async_session
    from app.services.system.rekey import rekey_secrets

    if not from_secret_key:
        console.print(
            "[yellow]Say where the OLD key comes from. Today the only "
            "source is --from-secret-key, which is what rows were sealed "
            "with before ENCRYPTION_KEY was set.[/]"
        )
        raise typer.Exit(code=2)
    if not settings.ENCRYPTION_KEY:
        console.print(
            "[red]ENCRYPTION_KEY is not set, so there is nothing to rotate "
            "TO. Put your new key in .env first, then run this.[/]"
        )
        raise typer.Exit(code=2)

    async def run() -> dict[str, int]:
        async with get_async_session() as session:
            moved = await rekey_secrets(
                session, old_key_material=settings.SECRET_KEY.encode("utf-8")
            )
            await session.commit()
            return moved

    moved = asyncio.run(run())
    for where, count in sorted(moved.items()):
        tone = "green" if count else "dim"
        console.print(f"[{tone}]{where}: {count} re-keyed[/]")
    if not any(moved.values()):
        console.print(
            "[yellow]Nothing moved. Either every row is already under this "
            "key, or the old key was not the SECRET_KEY fallback.[/]"
        )
