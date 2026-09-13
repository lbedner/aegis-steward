"""Document store: the paper, addressed by its own content

Revision ID: 008
Revises: 007
Create Date: 2026-09-13 19:16:45.008748

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create documents service tables."""

    # Create document table
    op.create_table(
        'document',



        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),



        sa.Column('owner_user_id', sa.Integer(), nullable=True),



        sa.Column('title', sa.String(255), nullable=False),



        sa.Column('kind', sa.String(32), nullable=False, default='other'),



        sa.Column('storage_key', sa.String(128), nullable=False),



        sa.Column('storage_backend', sa.String(32), nullable=False, default='filesystem'),



        sa.Column('content_hash', sa.String(64), nullable=False),



        sa.Column('media_type', sa.String(128), nullable=True),



        sa.Column('byte_size', sa.Integer(), nullable=False, default=0),



        sa.Column('page_count', sa.Integer(), nullable=True),



        sa.Column('document_date', sa.Date(), nullable=True),



        sa.Column('received_at', sa.DateTime(), nullable=True),



        sa.Column('source', sa.String(32), nullable=False, default='upload'),



        sa.Column('channel', sa.String(32), nullable=True),



        sa.Column('supersedes_id', sa.Integer(), nullable=True),



        sa.Column('protected', sa.Boolean(), nullable=False, default=False),



        sa.Column('note', sa.String(), nullable=True),



        sa.Column('meta_data', sa.JSON(), nullable=False, default={}),



        sa.Column('created_at', sa.DateTime(), nullable=False),



        sa.Column('updated_at', sa.DateTime(), nullable=True),



        sa.Column('deleted_at', sa.DateTime(), nullable=True),


        sa.PrimaryKeyConstraint('id'),



        sa.ForeignKeyConstraint(['supersedes_id'], ['document.id']),



        sa.CheckConstraint("kind IN ('letter', 'statement', 'form', 'identification', 'receipt', 'other')", name='ck_document_kind')


    )

    op.create_index(op.f('ix_document_owner'), 'document', ['owner_user_id'])

    op.create_index(op.f('ix_document_supersedes'), 'document', ['supersedes_id'])

    op.create_index(op.f('ix_document_owner_hash'), 'document', ['owner_user_id', 'content_hash'], unique=True, sqlite_where=sa.text("deleted_at IS NULL"), postgresql_where=sa.text("deleted_at IS NULL"))

    op.create_index(op.f('ix_document_kind'), 'document', ['kind'])



    # Create document_page table
    op.create_table(
        'document_page',



        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),



        sa.Column('document_id', sa.Integer(), nullable=False),



        sa.Column('page_number', sa.Integer(), nullable=False),



        sa.Column('status', sa.String(16), nullable=False, default='unread'),



        sa.Column('method', sa.String(16), nullable=False, default='none'),



        sa.Column('text', sa.String(), nullable=True),



        sa.Column('image_key', sa.String(128), nullable=True),



        sa.Column('model', sa.String(128), nullable=True),



        sa.Column('detail', sa.String(), nullable=True),



        sa.Column('created_at', sa.DateTime(), nullable=False),



        sa.Column('updated_at', sa.DateTime(), nullable=True),


        sa.PrimaryKeyConstraint('id'),



        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE')



    )

    op.create_index(op.f('ix_document_page_document'), 'document_page', ['document_id'])

    op.create_index(op.f('uq_document_page'), 'document_page', ['document_id', 'page_number'], unique=True)



    # Create document_tag table
    op.create_table(
        'document_tag',



        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),



        sa.Column('document_id', sa.Integer(), nullable=False),



        sa.Column('label', sa.String(64), nullable=False),


        sa.PrimaryKeyConstraint('id'),



        sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE')



    )

    op.create_index(op.f('ix_document_tag_document'), 'document_tag', ['document_id'])

    op.create_index(op.f('uq_document_tag'), 'document_tag', ['document_id', 'label'], unique=True)





def downgrade() -> None:
    """Reverse documents migration."""




    op.drop_index(op.f('ix_document_tag_document'), table_name='document_tag')

    op.drop_index(op.f('uq_document_tag'), table_name='document_tag')

    op.drop_table('document_tag')


    op.drop_index(op.f('ix_document_page_document'), table_name='document_page')

    op.drop_index(op.f('uq_document_page'), table_name='document_page')

    op.drop_table('document_page')


    op.drop_index(op.f('ix_document_owner'), table_name='document')

    op.drop_index(op.f('ix_document_supersedes'), table_name='document')

    op.drop_index(op.f('ix_document_owner_hash'), table_name='document')

    op.drop_index(op.f('ix_document_kind'), table_name='document')

    op.drop_table('document')

