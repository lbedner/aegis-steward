"""Matter fixtures' shared plumbing: find one, and put it back.

The app-owned engine is SESSION scoped, so a matter a test leaves behind
is a matter every later test sees - and "no matters yet" is an assertion
somebody else already wrote. Both matter test files had their own copy of
this, and the copies drifted: one of them forgot the document tags, which
SQLite then handed to the next matter created, because a deleted row's id
is handed out again. A case that had never been filed on read with a
paper on its shelf (2026-09-18).
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from tests.web.dom import select, text


def matter_referenced(client: Any, reference: str) -> int:
    """The id of the matter carrying this reference.

    By POSITION against the rows, because every earlier test's matter is
    still listed.
    """
    page = client.get("/matters").text
    rows = select(page, "#matters tbody tr")
    opens = select(page, "#matters tbody [data-open]")
    for row, open_ in zip(rows, opens, strict=False):
        if reference in text(row):
            return int(open_.get("hx-get").rsplit("/", 1)[-1])
    raise AssertionError(f"no matter referenced {reference}")


def forget_matter(matter_id: int) -> None:
    """Remove what a fixture made, straight through the engine the app
    writes to. There is no delete route for a matter, deliberately: a
    case is not something the UI should make easy to lose."""
    from sqlalchemy import text as sql

    from app.core.db import AsyncSessionLocal
    from app.services.matters.models import matter_tag

    async def clean() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                sql(
                    "delete from evidence_link where request_item_id in "
                    "(select ri.id from request_item ri join request r "
                    " on r.id = ri.request_id where r.matter_id = :m)"
                ),
                {"m": matter_id},
            )
            await db.execute(
                sql(
                    "delete from request_item where request_id in "
                    "(select id from request where matter_id = :m)"
                ),
                {"m": matter_id},
            )
            for table in ("request", "fact", "matter_event", "matter_participant"):
                await db.execute(
                    sql(f"delete from {table} where matter_id = :m"), {"m": matter_id}
                )
            # The shelf as well: the id comes back, and with it anything
            # still filed under the old matter's tag.
            await db.execute(
                sql("delete from document_tag where label = :tag"),
                {"tag": matter_tag(matter_id)},
            )
            await db.execute(sql("delete from matter where id = :m"), {"m": matter_id})
            await db.commit()

    asyncio.run(clean())


def account_held_by(
    party_id: int, name: str, posted: list[tuple[date, int]]
) -> int:
    """An account in somebody's name, with a register that says something.

    Written to the app-owned engine rather than through /accounts/new,
    because the two are different DATABASES in a web test: finance
    routes take the per-test session through ``get_async_db``, matters
    routes open their own against the app-owned engine, and the answer
    sheet reads the second. An account created through the dialog is
    invisible to the page under test.

    The dialog would not do it anyway: it writes a STATED balance
    (``current_balance``), and what the county asks for is a balance on
    a DATE, which only posted rows carry.
    """
    ids: dict[str, int] = {}

    async def make() -> None:
        from app.core.db import AsyncSessionLocal
        from app.services.finance.domains.ledger.subjects import (
            assign_subject,
            subject_for_party,
        )
        from app.services.finance.service import FinanceService
        from app.services.matters.service import PartyService

        async with AsyncSessionLocal() as db:
            svc = FinanceService(db)
            account = await svc.create_manual_account(
                name=name, account_type="checking", classification="asset"
            )
            party = await PartyService(db).get(party_id)
            subject = await subject_for_party(db, party_id, name=party.name)
            await assign_subject(db, account.id, subject.id)
            for on, cents in posted:
                await svc.create_transaction(
                    account_id=account.id, amount=cents, txn_date=on, name="Testcase"
                )
            await db.commit()
            ids["account"] = account.id

    asyncio.run(make())
    return ids["account"]
