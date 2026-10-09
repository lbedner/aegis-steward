"""seed_rows: insert what is missing by key, never touch what is there."""

import pytest
from sqlmodel import Session, col, select

from app.core.seed import missing_rows, seed_rows
from app.services.ai.models.agents import MemoryModule


def _module(slug: str, text: str = "static") -> dict[str, object]:
    return {"slug": slug, "name": slug, "prompt_content": text, "context_key": slug}


def test_inserts_only_the_missing_rows(db_session: Session) -> None:
    db_session.add(MemoryModule(**_module("present")))
    db_session.commit()

    added = seed_rows(
        db_session, MemoryModule, "slug", [_module("present"), _module("new")]
    )
    db_session.commit()

    assert added == 1
    slugs = sorted(db_session.exec(select(MemoryModule.slug)).all())
    assert slugs == ["new", "present"]


@pytest.mark.queryspy(threshold=3)  # seeds, then reads the rows back
def test_an_edited_row_survives_reseeding(db_session: Session) -> None:
    seed_rows(db_session, MemoryModule, "slug", [_module("kept", "seeded")])
    db_session.commit()
    row = db_session.exec(select(MemoryModule)).one()
    row.prompt_content = "edited"
    db_session.add(row)
    db_session.commit()

    assert seed_rows(db_session, MemoryModule, "slug", [_module("kept")]) == 0
    assert db_session.exec(select(MemoryModule)).one().prompt_content == "edited"


def test_where_scopes_what_counts_as_present(db_session: Session) -> None:
    db_session.add(MemoryModule(**_module("inactive"), is_active=False))
    db_session.commit()

    scoped = missing_rows(
        db_session,
        MemoryModule,
        "slug",
        [_module("inactive")],
        col(MemoryModule.is_active).is_(True),
    )

    assert [row["slug"] for row in scoped] == ["inactive"]
