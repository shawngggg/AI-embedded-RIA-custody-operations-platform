"""Case ownership: who claimed or was assigned a case

Revision ID: dfde31c129be
Revises: d94e95d449a0
Create Date: 2026-10-25 16:56:15.023818

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfde31c129be'
down_revision: Union[str, Sequence[str], None] = 'd94e95d449a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('cases', sa.Column('assigned_to', sa.Integer(), nullable=True))
    op.add_column('cases', sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_cases_assigned_to'), 'cases', ['assigned_to'], unique=False)
    op.create_foreign_key('cases_assigned_to_fkey', 'cases', 'users', ['assigned_to'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('cases_assigned_to_fkey', 'cases', type_='foreignkey')
    op.drop_index(op.f('ix_cases_assigned_to'), table_name='cases')
    op.drop_column('cases', 'assigned_at')
    op.drop_column('cases', 'assigned_to')
