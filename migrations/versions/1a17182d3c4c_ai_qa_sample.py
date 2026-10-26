"""AI review QA: a sample of reviewed AI-read values gets a second person's check

Revision ID: 1a17182d3c4c
Revises: 1ce0379d44cb
Create Date: 2026-10-25 22:24:59.426636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1a17182d3c4c'
down_revision: Union[str, Sequence[str], None] = '1ce0379d44cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ai_suggestions', sa.Column('qa_selected', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('ai_suggestions', sa.Column('qa_verdict', sa.String(length=20), nullable=True))
    op.add_column('ai_suggestions', sa.Column('qa_note', sa.Text(), nullable=True))
    op.add_column('ai_suggestions', sa.Column('qa_by_name', sa.String(length=120), nullable=True))
    op.add_column('ai_suggestions', sa.Column('qa_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ai_suggestions', 'qa_at')
    op.drop_column('ai_suggestions', 'qa_by_name')
    op.drop_column('ai_suggestions', 'qa_note')
    op.drop_column('ai_suggestions', 'qa_verdict')
    op.drop_column('ai_suggestions', 'qa_selected')
