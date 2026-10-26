"""Flagged list entries: list updates the sanctions team adds, which set off rescreening

Revision ID: d94e95d449a0
Revises: cf373bde83dc
Create Date: 2026-10-25 16:44:36.519614

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd94e95d449a0'
down_revision: Union[str, Sequence[str], None] = 'cf373bde83dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('flagged_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entry_id', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('aliases', sa.JSON(), nullable=False),
    sa.Column('date_of_birth', sa.String(length=10), nullable=True),
    sa.Column('country', sa.String(length=60), nullable=True),
    sa.Column('identifiers', sa.JSON(), nullable=False),
    sa.Column('program', sa.String(length=120), nullable=False),
    sa.Column('source', sa.String(length=200), nullable=False),
    sa.Column('added_by', sa.String(length=120), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name='flagged_entries_pkey'),
    sa.UniqueConstraint('entry_id', name='flagged_entries_entry_id_key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('flagged_entries')
