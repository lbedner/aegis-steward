"""A restore drill's checks, run against a restored copy by the app's own
code (``deploy drill-check``; ``aegis deploy-restore-drill`` runs it in a
throwaway container pointed at what it restored).

The copy is the settings' database (the container points ``DATABASE_URL``
at it). Each check is ``{"name", "ok", "said"}``: the file is whole, the
app's own startup brings it up (migrations as a restored server would run
them at boot), every stored secret decrypts with this ``ENCRYPTION_KEY``
(the key kept off the server is the right one), and every stored file a
row names is in the restored store. A copy that is damaged, or will not
start, is checked no further: the rest would read what it does not have.
"""

import asyncio
import sqlite3
from typing import Any

from app.core.db import DATABASE_PATH, get_async_session
from app.core.storage import missing_objects


def _whole() -> dict[str, Any]:
    try:
        db = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            found = db.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            db.close()
    except sqlite3.DatabaseError as exc:
        found = str(exc)
    return {"name": "database", "ok": found == "ok", "said": f"integrity: {found}"}


async def _started() -> dict[str, Any]:
    """The app's own startup on the copy, as a restored server boots."""
    from app.components.backend.startup.database_init import startup_database_init

    try:
        await startup_database_init()
    except Exception as exc:
        said = str(exc).strip().splitlines()[-1:] or [type(exc).__name__]
        return {"name": "startup", "ok": False, "said": said[0][:300]}
    return {"name": "startup", "ok": True, "said": "Started as the app boots"}


async def _secrets() -> dict[str, Any]:
    try:
        from app.components.secrets.store import DatabaseStore
    except ImportError:  # no secrets component: nothing stored to read
        return {"name": "secrets", "ok": True, "said": "No secrets store."}
    from app.core.secrets import SecretUnreadableError

    store = DatabaseStore()
    names = list(await store.stored())
    try:
        await store.get_many(names)
    except SecretUnreadableError as exc:  # names the first that will not
        return {"name": "secrets", "ok": False, "said": str(exc)}
    return {"name": "secrets", "ok": True, "said": f"{len(names)} read"}


async def _files() -> dict[str, Any]:
    async with get_async_session() as session:
        missing = await missing_objects(session)
    if missing:
        said = f"{len(missing)} stored file{'s' if len(missing) != 1 else ''} missing"
        return {"name": "files", "ok": False, "said": said}
    return {"name": "files", "ok": True, "said": "Every stored file present"}


def read_alone() -> None:
    """The copy read on its own cache: a throwaway container has no Redis,
    and a cache shared with a running stack could answer with that stack's
    values instead of the copy's (``deploy drill-check`` calls this)."""
    import app.core.cache as shared

    shared.cache = shared.CacheService(redis_url=None)


async def drill_check() -> list[dict[str, Any]]:
    """Each check against the copy and the store the app is pointed at,
    up to the first that leaves nothing for the rest to read."""
    checks = [await asyncio.to_thread(_whole)]
    if checks[-1]["ok"]:
        checks.append(await _started())
    if checks[-1]["ok"]:
        checks += [await _secrets(), await _files()]
    return checks
