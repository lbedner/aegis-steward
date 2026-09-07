"""Audit-file writer for ops setup commands.

Writes ``.aegis/email-setup.json`` (and, in the future, sibling files
for other ops actions) so the project carries a record of *what was
provisioned, when, against which vendor*. The goal: six months from
now, anyone reading the project — human or agent — can answer
"why does this TXT record exist?" by reading one file, no archaeology
required.

The file is append-friendly: every run lands as a new entry in a
top-level ``history`` array rather than overwriting the last run.
That way, replays / re-verifies / failed-then-fixed sequences all
leave a trail.

Stored under ``.aegis/`` rather than committed to source — it's local
operational state, not source. The project's ``.gitignore`` already
ignores ``.aegis/``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any

from app.services.ops.types import EmailSetupResult

logger = logging.getLogger(__name__)

AUDIT_FILE_VERSION = 1
DEFAULT_AUDIT_PATH = Path(".aegis") / "email-setup.json"


def _json_default(obj: Any) -> Any:
    """JSON encoder for the types ``EmailSetupResult`` contains.

    ``datetime`` -> ISO 8601 with explicit ``Z`` for UTC so the file
    stays unambiguous when read by a human in six months.
    """
    if isinstance(obj, datetime):
        return obj.isoformat().replace("+00:00", "Z")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


def write_audit(result: EmailSetupResult, *, path: Path = DEFAULT_AUDIT_PATH) -> Path:
    """Append ``result`` to the audit file, creating the file if missing.

    Returns the path written to. Idempotent on the file level (always
    appends; never destructive). Safe to call on every run, including
    re-runs of the same domain — re-verifies want their own history
    entry too.
    """
    path = Path(path)
    # ``DEFAULT_AUDIT_PATH`` is relative; the CLI doesn't chdir, so
    # naively writing relative to cwd scatters audit logs across the
    # tree when ops commands run from a subdirectory. Resolve against
    # the nearest ``pyproject.toml`` ancestor instead.
    if not path.is_absolute():
        cwd = Path.cwd().resolve()
        root = next(
            (p for p in [cwd, *cwd.parents] if (p / "pyproject.toml").is_file()),
            cwd,
        )
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = asdict(result)
    entry = {
        "ran_at": _json_default(result.finished_at),
        "result": payload,
    }

    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            # File got corrupted (manually edited / partial write).
            # Don't drop the data — rename the corrupt file aside and
            # start fresh. Operator can reconcile from the .bak later.
            backup = path.with_suffix(path.suffix + ".bak")
            logger.warning(
                "ops.audit: existing %s is not valid JSON; renaming to %s "
                "and starting a fresh audit file.",
                path,
                backup,
            )
            path.rename(backup)
            existing = None
    else:
        existing = None

    if existing is None:
        doc: dict[str, Any] = {
            "version": AUDIT_FILE_VERSION,
            "history": [entry],
        }
    else:
        doc = existing
        doc.setdefault("version", AUDIT_FILE_VERSION)
        doc.setdefault("history", []).append(entry)

    path.write_text(
        json.dumps(doc, indent=2, default=_json_default, sort_keys=False) + "\n"
    )
    return path
