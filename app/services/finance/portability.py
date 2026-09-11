"""The finance service's data, out of one instance and into another.

A statement import carries transactions. Everything a ledger becomes
around them — the accounts, the bills and budgets, the categories and the
aliases that learned where a payee belongs, the payee icons, the tags,
the valuations and holdings — lives only in the database, and moving
between instances (or between the Flet app and this one) needs all of it.

The archive is gzipped JSONL and is written and read from the model
metadata rather than a hand-kept list of tables, so a new model or column
travels without anyone remembering to add it here. One header line names
the format and the tables; every line after it is one row:

    {"table": "finance_account", "row": {...}}

Rows keep their primary keys, because half the meaning is in what points
at what. Values that JSON has no word for (dates, decimals, bytes) are
written as strings and read back through the column's own type.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
import gzip
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Table, bindparam, delete, insert, inspect, select, update
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance import models  # noqa: F401  (registers the tables)

FORMAT = "aegis-finance-export"
VERSION = 1
# Rows per executemany. Big enough that 275,000 import-audit rows are a
# handful of round trips, small enough that none of it is held twice.
CHUNK = 2000


def finance_tables() -> list[Table]:
    """Every finance table, parents before children.

    ``sorted_tables`` is SQLAlchemy's own dependency sort, so a restore
    inserts in an order the foreign keys can accept — except for the
    links a transaction makes to another transaction (a transfer pair,
    a canonical row), which no table order can satisfy. Those are why a
    restore defers constraint checking rather than trusting the order.
    """
    return [
        table
        for table in SQLModel.metadata.sorted_tables
        if table.name.startswith("finance_")
    ]


def _encode(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return value


def _decode(value: Any, column: Any) -> Any:
    """A JSON value back into what its column holds."""
    if value is None:
        return None
    try:
        python_type = column.type.python_type
    except NotImplementedError:  # a type that never round-trips through JSON
        return value
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if python_type is date and isinstance(value, str):
        return date.fromisoformat(value)
    if python_type is time and isinstance(value, str):
        return time.fromisoformat(value)
    if python_type is Decimal and isinstance(value, str):
        return Decimal(value)
    if python_type is bytes and isinstance(value, str):
        return base64.b64decode(value)
    return value


async def reflect(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """What this database actually has: columns and foreign keys per table.

    Reflected, not read off the models, and for both directions. An
    export usually reads an OLDER instance than the code doing the
    reading — that is what moving a ledger between instances means. And a
    migration may declare keys the model never did: the transaction table
    carries twelve foreign keys in the schema and seven in the class, and
    it is the schema that refuses a bad row.
    """
    connection = await session.connection()
    wanted = [(table.name, table.schema) for table in finance_tables()]

    def _read(sync_connection: Any) -> dict[str, dict[str, Any]]:
        inspector = inspect(sync_connection)
        found: dict[str, dict[str, Any]] = {}
        for name, schema in wanted:
            if not inspector.has_table(name, schema=schema):
                continue
            columns = {
                column["name"]: bool(column["nullable"])
                for column in inspector.get_columns(name, schema=schema)
            }
            keys: dict[str, tuple[str, str]] = {}
            for key in inspector.get_foreign_keys(name, schema=schema):
                for local, remote in zip(
                    key["constrained_columns"], key["referred_columns"], strict=False
                ):
                    keys[local] = (key["referred_table"], remote)
            found[name] = {"columns": columns, "keys": keys}
        return found

    return await connection.run_sync(_read)


async def export_finance(session: AsyncSession, path: Path) -> dict[str, int]:
    """Write every finance row to ``path``. Returns rows per table.

    Streamed a chunk at a time: the transaction and import-audit tables
    run to hundreds of thousands of rows, and none of them belongs in
    memory at once.
    """
    counts: dict[str, int] = {}
    tables = finance_tables()
    with gzip.open(path, "wt", encoding="utf-8") as archive:
        header = {
            "format": FORMAT,
            "version": VERSION,
            "created_at": datetime.now().astimezone().isoformat(),
            "tables": [table.name for table in tables],
        }
        archive.write(json.dumps(header) + "\n")
        shape = await reflect(session)
        for table in tables:
            live = shape.get(table.name, {}).get("columns", {})
            columns = [column for column in table.columns if column.name in live]
            if not columns:
                continue
            written = 0
            result = await session.stream(
                select(*columns).execution_options(yield_per=CHUNK)
            )
            async for row in result:
                payload = {key: _encode(value) for key, value in row._mapping.items()}
                archive.write(json.dumps({"table": table.name, "row": payload}) + "\n")
                written += 1
            if written:
                counts[table.name] = written
    return counts


def read_header(path: Path) -> dict[str, Any]:
    """The archive's first line, so a restore can refuse a file that is
    not one of ours before it deletes anything."""
    with gzip.open(path, "rt", encoding="utf-8") as archive:
        header = json.loads(archive.readline() or "{}")
    if header.get("format") != FORMAT:
        raise ValueError(f"{path.name} is not an {FORMAT} archive.")
    if header.get("version") != VERSION:
        raise ValueError(
            f"{path.name} is version {header.get('version')}; this reads {VERSION}."
        )
    return header


def _rows(path: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    with gzip.open(path, "rt", encoding="utf-8") as archive:
        archive.readline()  # the header, already read and checked
        for line in archive:
            if not line.strip():
                continue
            entry = json.loads(line)
            yield entry["table"], entry["row"]


async def _apply_deferred(
    session: AsyncSession, table: Table, rows: list[dict[str, Any]]
) -> None:
    """Set the self-references held back during the load."""
    if not rows:
        return
    keys = list(table.primary_key.columns)
    if len(keys) != 1:  # every self-referencing table has a simple key
        return
    columns = [name for name in rows[0] if name != "_pk"]
    statement = (
        update(table)
        .where(keys[0] == bindparam("_pk"))
        .values({name: bindparam(name) for name in columns})
    )
    for at in range(0, len(rows), CHUNK):
        await session.execute(statement, rows[at : at + CHUNK])


async def restore_finance(
    session: AsyncSession, path: Path, *, replace: bool = False
) -> tuple[dict[str, int], dict[str, int]]:
    """Load an archive written by ``export_finance``.

    Returns the rows loaded per table, and the repairs made on the way.

    ``replace`` empties the finance tables first (children before
    parents); without it the load is additive and a primary key that is
    already taken fails, which is the honest outcome — two ledgers merged
    by luck is worse than a refusal.

    Two things no insert order can fix happen here. A reference that
    points forward or at its own table (a transfer's two transactions
    name each other; a transaction names the transfer group it belongs
    to) is left empty and set once every row exists. And a reference with
    nothing at the end of it — a transaction filed under a recurring
    stream someone deleted, which the source tolerated because a database
    only enforces the keys it was given — is dropped and counted rather
    than failing the whole load. Nothing but the pointer is lost.
    """
    read_header(path)
    tables = {table.name: table for table in finance_tables()}
    position = {name: index for index, name in enumerate(tables)}
    shape = await reflect(session)

    keys_by_table: dict[str, dict[str, tuple[str, str, bool]]] = {}
    deferred_columns: dict[str, dict[str, tuple[str, str]]] = {}
    for name, table_shape in shape.items():
        nullable = table_shape["columns"]
        keys_by_table[name] = {
            local: (target, remote, nullable.get(local, True))
            for local, (target, remote) in table_shape["keys"].items()
        }
        deferred_columns[name] = {
            local: (target, remote)
            for local, (target, remote) in table_shape["keys"].items()
            if nullable.get(local, True)
            and position.get(target, -1) >= position.get(name, 0)
        }

    referenced: dict[str, set[str]] = {}
    for keys in keys_by_table.values():
        for target, remote, _ in keys.values():
            referenced.setdefault(target, set()).add(remote)
    seen: dict[tuple[str, str], set[Any]] = {
        (table_name, column): set()
        for table_name, columns in referenced.items()
        for column in columns
    }
    deferred: dict[str, list[dict[str, Any]]] = {}
    repairs: dict[str, int] = {}

    if replace:
        for table in reversed(finance_tables()):
            await session.execute(delete(table))

    counts: dict[str, int] = {}
    pending: list[dict[str, Any]] = []
    current: str | None = None
    for name, row in _rows(path):
        table = tables.get(name)
        if table is None or name not in shape:  # a table this build lacks
            continue
        if name != current and pending and current:
            await _flush(session, tables[current], pending)
            pending = []
        current = name
        values = {
            key: _decode(value, table.columns[key])
            for key, value in row.items()
            if key in table.columns
        }

        held = {
            column: values.pop(column)
            for column in deferred_columns[name]
            if values.get(column) is not None
        }
        orphaned = False
        for column, (target, remote, nullable) in keys_by_table[name].items():
            value = values.get(column)
            if value is None or value in seen.get((target, remote), ()):
                continue
            if nullable:
                values[column] = None
            else:
                orphaned = True
            repairs[f"{name}.{column}"] = repairs.get(f"{name}.{column}", 0) + 1
        if orphaned:
            continue

        primary = list(table.primary_key.columns)
        if held:
            held["_pk"] = values[primary[0].name]
            deferred.setdefault(name, []).append(held)
        for column in deferred_columns[name]:
            values.setdefault(column, None)
        for column in referenced.get(name, ()):
            if column in values:
                seen[(name, column)].add(values[column])
        pending.append(values)
        counts[name] = counts.get(name, 0) + 1
        if len(pending) >= CHUNK:
            await _flush(session, table, pending)
            pending = []
    if pending and current:
        await _flush(session, tables[current], pending)

    for name, rows in deferred.items():
        kept: list[dict[str, Any]] = []
        for row in rows:
            repaired: dict[str, Any] = {"_pk": row["_pk"]}
            for column, value in row.items():
                if column == "_pk":
                    continue
                target, remote = deferred_columns[name][column]
                if value in seen.get((target, remote), ()):
                    repaired[column] = value
                else:
                    repaired[column] = None
                    key = f"{name}.{column}"
                    repairs[key] = repairs.get(key, 0) + 1
            kept.append(repaired)
        await _apply_deferred(session, tables[name], kept)
    await session.commit()
    return counts, repairs


async def _flush(
    session: AsyncSession, table: Table, rows: list[dict[str, Any]]
) -> None:
    if rows:
        await session.execute(insert(table), rows)
