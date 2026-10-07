"""A bank's website lives on its contact, once (#412)

The bank row kept its own homepage (``url``, and ``domain`` derived from
it) and a phone in ``metadata``, beside the contact that holds the same
facts. A website set on the contact - by a card in chat - left the bank's
logo and link unchanged. Each fact moves to the contact where the
contact has none (a bank without a contact gets one), and the bank
row's copies go.

Revision ID: 055
Revises: 054
Create Date: 2026-10-06 20:30:00.000000
"""

from datetime import UTC, datetime
import json
from typing import Any

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None

aegis_stamp_signature = ("no_column", "finance_institution", "url")


def _json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        found = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        found = None
    return found if isinstance(found, dict) else {}


def _domain(url: str | None) -> str | None:
    """What the icon resolver keyed on: no scheme, no www., no path."""
    if not url:
        return None
    host = url.strip().split("://", 1)[-1].split("/", 1)[0].lower()
    return host.removeprefix("www.") or None


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None)
    banks = bind.execute(
        sa.text(
            "SELECT id, owner_user_id, name, party_id, url, metadata "
            "FROM finance_institution"
        )
    ).all()
    for bank_id, owner, name, party_id, url, metadata in banks:
        extra = _json(metadata)
        phone = str(extra.pop("phone", "") or "").strip()
        reach = {k: v for k, v in (("website", url), ("phone", phone)) if v}
        if party_id is None and reach:
            party_id = bind.execute(
                sa.text(
                    "INSERT INTO party (owner_user_id, kind, name, sort_name, "
                    "contact, created_at, updated_at) VALUES (:owner, "
                    "'organization', :name, :name, :contact, :now, :now) "
                    "RETURNING id"
                ),
                {
                    "owner": owner,
                    "name": name,
                    "contact": json.dumps(reach),
                    "now": now,
                },
            ).scalar_one()
            bind.execute(
                sa.text("UPDATE finance_institution SET party_id = :p WHERE id = :i"),
                {"p": party_id, "i": bank_id},
            )
        elif party_id is not None and reach:
            row = bind.execute(
                sa.text("SELECT contact FROM party WHERE id = :p"), {"p": party_id}
            ).first()
            contact = _json(row[0] if row else None)
            # Fill blanks only: what somebody set on the contact stands.
            merged = {**reach, **{k: v for k, v in contact.items() if v}}
            if merged != contact:
                bind.execute(
                    sa.text("UPDATE party SET contact = :c WHERE id = :p"),
                    {"c": json.dumps(merged), "p": party_id},
                )
        if "phone" in _json(metadata):
            bind.execute(
                sa.text("UPDATE finance_institution SET metadata = :m WHERE id = :i"),
                {"m": json.dumps(extra), "i": bank_id},
            )
    with op.batch_alter_table("finance_institution", schema=None) as batch_op:
        batch_op.drop_column("url")
        batch_op.drop_column("domain")


def downgrade() -> None:
    with op.batch_alter_table("finance_institution", schema=None) as batch_op:
        batch_op.add_column(sa.Column("url", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("domain", sa.String(length=255), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT i.id, p.contact FROM finance_institution i "
            "JOIN party p ON p.id = i.party_id"
        )
    ).all()
    for bank_id, contact in rows:
        site = _json(contact).get("website")
        if site:
            bind.execute(
                sa.text(
                    "UPDATE finance_institution SET url = :u, domain = :d WHERE id = :i"
                ),
                {"u": site, "d": _domain(site), "i": bank_id},
            )
