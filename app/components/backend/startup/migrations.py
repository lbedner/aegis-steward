"""Migrations build the schema. The server does not.

``startup_database_init`` used to run ``SQLModel.metadata.create_all``
over every imported model, which reads as harmless and is not: the
moment a new model becomes importable, the dev server's next reload
CREATES its table, and alembic afterwards finds the table already there
with the version lagging behind. It cost a hand-stamp twice - finance_icon,
then ``insurance_policy`` and ``insurance_claim`` stamped by hand at 026
(2026-09-17) - and ``_check_schema_mismatch`` could never catch it,
because it ran after the create_all that hid the evidence.

So when ``alembic/versions`` exists, this module owns the schema:

- ``adopt_pending`` stamps a migration whose objects ALREADY EXIST,
  rather than replaying its DDL. A persisted database that predates a
  migration is the normal case, not a broken one, and replaying is what
  makes a boot log "already exists" forever.
- ``upgrade_to_head`` runs the rest.
- ``missing_model_tables`` is the loud part: a model whose table no
  migration creates stops startup and names the table. That is the
  failure create_all used to paper over, and the whole point of the
  ticket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.log import logger

VERSIONS = Path("alembic") / "versions"
CONFIG = Path("alembic") / "alembic.ini"


def versions_exist() -> bool:
    """Whether this install ships migrations at all.

    A project generated without them has nothing to upgrade to, and
    ``create_all`` remains the only way its tables appear.
    """
    return VERSIONS.is_dir() and any(VERSIONS.glob("*.py"))


def _bare(name: str) -> str:
    """A table name without its schema.

    Signatures may be schema-qualified (``finance.finance_holding``)
    because Postgres projects put components in their own schemas.
    SQLite has none, so matching has to tolerate the bare name.
    """
    return name.split(".")[-1]


def already_applied(inspector: Any, signature: tuple[str, ...]) -> bool:
    """Whether the object a migration would create is already there.

    The proof shapes live in ``migration_signatures``. A shape nobody
    defined reads as NOT applied, deliberately: replaying DDL is noisy
    and recoverable, while stamping a migration that never ran leaves
    the schema short a table and stamped as complete, which is not.
    """
    try:
        kind = signature[0]
        if kind == "table":
            return _bare(signature[1]) in set(inspector.get_table_names())
        if kind in ("column", "foreign_key"):
            table = _bare(signature[1])
            if table not in set(inspector.get_table_names()):
                return False
            if kind == "column":
                names = {col["name"] for col in inspector.get_columns(table)}
                return signature[2] in names
            covered = {
                column
                for key in inspector.get_foreign_keys(table)
                for column in key.get("constrained_columns", [])
            }
            return signature[2] in covered
        if kind == "check":
            table = _bare(signature[1])
            if table not in set(inspector.get_table_names()):
                return False
            wanted = signature[3] if len(signature) > 3 else ""
            for constraint in inspector.get_check_constraints(table):
                if constraint.get("name") == signature[2]:
                    return wanted in str(constraint.get("sqltext", ""))
            return False
    except Exception as e:  # noqa: BLE001 - an unreadable schema is "not proven"
        logger.debug(f"Signature check failed for {signature}: {e}")
    return False


# Tables a model declares that alembic deliberately does NOT create,
# because something else owns them. APScheduler builds its own job store
# on first run; a migration for it would fight the library over the
# schema. Everything else in the metadata is the app's, and a name that
# turns up missing is a model somebody added without a migration.
#
# Keep this list short and reasoned. It is the escape hatch that makes
# the check below trustworthy, so an entry added to quieten a failure
# rather than to name a real external owner defeats the whole guard.
EXTERNALLY_OWNED = frozenset({"apscheduler_jobs"})


def missing_model_tables(inspector: Any, expected: set[str]) -> set[str]:
    """Model tables that nothing has created.

    After the migrations have run this must be empty. A name in here is
    a model somebody added without a migration, and the old behaviour -
    creating it on the way past - is exactly what put the version
    number behind the schema.
    """
    existing = {_bare(name) for name in inspector.get_table_names()}
    return (
        {_bare(name) for name in expected}
        - existing
        - {"alembic_version"}
        - EXTERNALLY_OWNED
    )


def _alembic_config(database_url: str) -> Any:
    from alembic.config import Config

    config = Config(str(CONFIG))
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _url_for(database_path: str) -> str:
    return f"sqlite:///{database_path}"


def upgrade_to_head(database_path: str) -> None:
    """Run the migrations. The only thing that builds tables."""
    from alembic import command

    command.upgrade(_alembic_config(_url_for(database_path)), "head")


def _pending(script: Any, current: str | None) -> list[Any]:
    """Revisions from ``current`` to head, OLDEST FIRST.

    ``walk_revisions`` answers newest-first, and adoption has to go the
    other way: each migration may only be adopted once everything before
    it has been.
    """
    ordered = list(script.walk_revisions("base", "heads"))[::-1]
    if current is None:
        return ordered
    seen = False
    pending = []
    for revision in ordered:
        if seen:
            pending.append(revision)
        if revision.revision == current:
            seen = True
    return pending if seen else ordered


def _service_of(revision: Any) -> str:
    """The ``NNN_<service>.py`` name a signature is keyed by."""
    name = Path(str(revision.path or "")).name
    return name[4:-3] if name[:3].isdigit() else ""


def _signature_for_revision(
    revision: Any, legacy_signatures: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    """Prefer the signature declared by a generated migration."""
    carried = getattr(getattr(revision, "module", None), "aegis_stamp_signature", None)
    return (
        carried if carried is not None else legacy_signatures.get(_service_of(revision))
    )


def adopt_pending(database_path: str) -> list[str]:
    """Stamp migrations whose objects a persisted database already has.

    Returns what was adopted, for the log. Nothing to adopt is the
    normal case and returns an empty list.

    Walks FORWARD from the current revision and stops at the first
    migration that cannot prove itself, stamping only as far as it got.
    That stopping matters: stamping straight to head because an EARLIER
    migration was adoptable would skip a later one whose DDL never ran,
    leaving the schema short a table and marked complete - the failure
    this whole module exists to make impossible.
    """
    from sqlalchemy import create_engine, inspect

    from alembic import command
    from alembic.script import ScriptDirectory
    from app.components.backend.startup.migration_signatures import (
        SERVICE_MIGRATION_SIGNATURES,
    )

    config = _alembic_config(_url_for(database_path))
    script = ScriptDirectory.from_config(config)
    engine = create_engine(_url_for(database_path))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if not tables:
            return []  # brand new: nothing to adopt, let the upgrade run
        current: str | None = None
        if "alembic_version" in tables:
            with engine.connect() as conn:
                from sqlalchemy import text

                row = conn.execute(
                    text("select version_num from alembic_version")
                ).first()
                current = row[0] if row else None

        adopted: list[str] = []
        furthest: str | None = None
        for revision in _pending(script, current):
            service = _service_of(revision)
            signature = _signature_for_revision(revision, SERVICE_MIGRATION_SIGNATURES)
            if signature is None or not already_applied(inspector, signature):
                break
            adopted.append(service)
            furthest = revision.revision
        if furthest is not None:
            command.stamp(config, furthest)
    finally:
        engine.dispose()
    return adopted
