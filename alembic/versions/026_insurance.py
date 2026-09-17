"""A policy and its claims have a home

Revision ID: 026
Revises: 025
Create Date: 2026-09-17 01:30:00.000000

Six Delta Dental documents were read in one turn - the plan, the
invoice, the claim statement, the welcome letter, the notices, the
88-page contract - and every figure in them ended up in a note on the
contact, because an insurer was a contact and nothing else existed. A
note cannot be queried, totalled or attached to.

A policy holds what the plan documents say: who wrote it, who it
covers, its numbers and dates, the stream its premium is paid through,
and its terms as the labelled lines the documents use (they differ by
kind, so JSON). A claim holds what an EOB says: the visit, the provider,
and the four figures - billed, allowed, insurer paid, patient owes. The
last is owed to the provider, "this is not a bill", which is exactly why
it needs a row of its own.
"""

import sqlalchemy as sa

from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None

POLICY_KINDS = ("dental", "health", "vision", "auto", "home", "life", "other")
CLAIM_STATUSES = ("submitted", "processed", "denied", "appealed", "paid")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(f'{v!r}' for v in values)})"


def upgrade() -> None:
    op.create_table(
        "insurance_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("insurer_party_id", sa.Integer(), nullable=False),
        sa.Column("covered_party_ids", sa.JSON(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("policy_number", sa.String(length=64), nullable=True),
        sa.Column("member_id", sa.String(length=64), nullable=True),
        sa.Column("group_id", sa.String(length=64), nullable=True),
        sa.Column("effective_on", sa.Date(), nullable=True),
        sa.Column("renews_on", sa.Date(), nullable=True),
        sa.Column("premium_stream_id", sa.Integer(), nullable=True),
        sa.Column("terms", sa.JSON(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            _one_of("kind", POLICY_KINDS), name="ck_insurance_policy_kind"
        ),
    )
    for name, cols in (
        ("ix_insurance_policy_owner", ["owner_user_id"]),
        ("ix_insurance_policy_insurer", ["insurer_party_id"]),
        ("ix_insurance_policy_stream", ["premium_stream_id"]),
        ("ix_insurance_policy_deleted", ["deleted_at"]),
    ):
        op.create_index(name, "insurance_policy", cols)

    op.create_table(
        "insurance_claim",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("covered_party_id", sa.Integer(), nullable=False),
        sa.Column("service_on", sa.Date(), nullable=False),
        sa.Column("provider_party_id", sa.Integer(), nullable=True),
        sa.Column("claim_number", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("billed_cents", sa.Integer(), nullable=False),
        sa.Column("allowed_cents", sa.Integer(), nullable=False),
        sa.Column("insurer_paid_cents", sa.Integer(), nullable=False),
        sa.Column("patient_owes_cents", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            _one_of("status", CLAIM_STATUSES), name="ck_insurance_claim_status"
        ),
    )
    for name, cols in (
        ("ix_insurance_claim_policy", ["policy_id"]),
        ("ix_insurance_claim_covered", ["covered_party_id"]),
        ("ix_insurance_claim_provider", ["provider_party_id"]),
        ("ix_insurance_claim_document", ["document_id"]),
        ("ix_insurance_claim_deleted", ["deleted_at"]),
    ):
        op.create_index(name, "insurance_claim", cols)


def downgrade() -> None:
    op.drop_table("insurance_claim")
    op.drop_table("insurance_policy")
