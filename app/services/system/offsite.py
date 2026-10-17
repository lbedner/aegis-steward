"""The nightly backup, copied off the server.

The 02:00 backup (``backup``) stays on the server's disk, so losing the
server loses it too. At 02:30 restic sends its newest database snapshot
and documents archive to a repository elsewhere (a DigitalOcean Spaces
bucket, or any S3), encrypted before it leaves, then thins the copies
there. Nothing runs until a repository is set (Overseer > Secrets).

Restoring, anywhere with restic and the same four values (as
``RESTIC_REPOSITORY``, ``RESTIC_PASSWORD``, ``AWS_ACCESS_KEY_ID``,
``AWS_SECRET_ACCESS_KEY``): ``restic restore latest --target <dir>``.
Without the password the copies cannot be read, and without the app's
``ENCRYPTION_KEY`` neither can its encrypted fields: keep both off the
server too.
"""

import asyncio
from datetime import UTC, datetime
import json
import os
from typing import Any

from app.core import secrets
from app.core.log import logger
from app.core.secrets import Secret
from app.services.system.backup import (
    BACKUP_FILE_PREFIX,
    BACKUP_FILE_SUFFIX,
    STORAGE_BACKUP_PREFIX,
    STORAGE_BACKUP_SUFFIX,
    backup_dir,
    list_backups,
)

OWNER = "Off-site backup"
SECRETS = (
    Secret(
        "OFFSITE_BACKUP_REPOSITORY",
        owner=OWNER,
        label="Where the copies go, as restic names it: "
        "s3:https://nyc3.digitaloceanspaces.com/<bucket>/<project>",
        secret=False,
    ),
    Secret(
        "OFFSITE_BACKUP_PASSWORD",
        owner=OWNER,
        label="Encrypts the copies. Keep it off this server too: "
        "without it they cannot be read",
    ),
    Secret("OFFSITE_BACKUP_ACCESS_KEY", owner=OWNER, label="The bucket's access key"),
    Secret("OFFSITE_BACKUP_SECRET_KEY", owner=OWNER, label="The bucket's secret key"),
)
# Each setting, as restic and its S3 client read it.
RESTIC_ENV = {
    "OFFSITE_BACKUP_REPOSITORY": "RESTIC_REPOSITORY",
    "OFFSITE_BACKUP_PASSWORD": "RESTIC_PASSWORD",
    "OFFSITE_BACKUP_ACCESS_KEY": "AWS_ACCESS_KEY_ID",
    "OFFSITE_BACKUP_SECRET_KEY": "AWS_SECRET_ACCESS_KEY",
}
KEEP = ("--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6")
# The last run, beside the backups it sends, for Overseer > Deployments >
# Backups: on the volume, so it outlives the worker's day-long task record.
RECORD = "offsite.json"


def last_run() -> dict[str, Any] | None:
    """What the last run did (``at``, ``ok``, ``stamp``, and ``copies`` and
    ``bytes`` in the repository, or ``error``); None before the first."""
    try:
        return json.loads((backup_dir() / RECORD).read_text())
    except (OSError, ValueError):
        return None


def _keep(**run: Any) -> None:
    (backup_dir() / RECORD).write_text(
        json.dumps({"at": datetime.now(UTC).isoformat(), **run})
    )


async def _restic(env: dict[str, str], *args: str, check: bool = True) -> str | None:
    """``restic args`` with the repository in ``env``: its output, or None
    when ``check`` is off and it failed."""
    done = await asyncio.create_subprocess_exec(
        "restic",
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await done.communicate()
    if done.returncode and check:
        raise RuntimeError(f"restic {args[0]} failed: {err.decode().strip()}")
    return out.decode() if done.returncode == 0 else None


async def offsite_backup_job() -> None:
    """Send the newest backup off the server, then thin the copies there."""
    found = await secrets.get_many(*RESTIC_ENV)
    if not found["OFFSITE_BACKUP_REPOSITORY"]:
        logger.info("Off-site backup is not set up: no repository")
        return
    if missing := [name for name, value in found.items() if not value]:
        raise RuntimeError(f"Off-site backup needs {', '.join(missing)}")
    newest = list_backups(backup_dir())
    if not newest:
        raise RuntimeError(f"No backup to send in {backup_dir()}")
    stamp = (
        newest[0].name.removeprefix(BACKUP_FILE_PREFIX).removesuffix(BACKUP_FILE_SUFFIX)
    )
    store = newest[0].with_name(
        f"{STORAGE_BACKUP_PREFIX}{stamp}{STORAGE_BACKUP_SUFFIX}"
    )
    sent = [str(newest[0]), *([str(store)] if store.exists() else [])]
    env = os.environ | {RESTIC_ENV[name]: str(value) for name, value in found.items()}
    try:
        # A new repository is made on the first run; ``cat config`` is the
        # cheapest read that says whether there is one.
        if await _restic(env, "cat", "config", check=False) is None:
            await _restic(env, "init")
        await _restic(env, "backup", "--tag", stamp, *sent)
        await _restic(env, "forget", *KEEP, "--prune")
        copies = json.loads(await _restic(env, "snapshots", "--json") or "[]")
        size = json.loads(
            await _restic(env, "stats", "--mode", "raw-data", "--json") or "{}"
        )
    except RuntimeError as exc:
        _keep(ok=False, stamp=stamp, error=str(exc))
        raise
    _keep(ok=True, stamp=stamp, copies=len(copies), bytes=size.get("total_size"))
    logger.info("Off-site backup sent", stamp=stamp, files=len(sent))
