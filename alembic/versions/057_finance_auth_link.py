"""Link finance owner_user_id columns to user.id (auth + finance)

Revision ID: 057
Revises: 056
Create Date: 2026-10-12 00:07:48.435948

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}
    role_column = ", role" if "role" in columns else ""
    role_value = ", 'user'" if "role" in columns else ""
    op.execute(
        f'INSERT INTO "user" '
        f"(id, email, is_active, is_verified, hashed_password, "
        f"failed_login_attempts, created_at{role_column}) "
        # Boolean literals, not 0: Postgres refuses an integer in a boolean
        # column and SQLite accepts FALSE (3.23+), so one serves both.
        f"SELECT 0, 'standalone@finance.local', FALSE, FALSE, '!', 0, "
        f"CURRENT_TIMESTAMP{role_value} "
        f'WHERE NOT EXISTS (SELECT 1 FROM "user" WHERE id = 0)'
    )


def downgrade() -> None:
    pass
