"""Quarterly account statements: the PDF, its fingerprint, and its summary

Revision ID: 9cad2ee67d74
Revises: 1a17182d3c4c
Create Date: 2026-10-25 22:28:03.676996

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9cad2ee67d74'
down_revision: Union[str, Sequence[str], None] = '1a17182d3c4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('statements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.String(length=40), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('filename', sa.String(length=120), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('content', sa.LargeBinary(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=False),
    sa.Column('generated_by', sa.String(length=120), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notices', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name='statements_account_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='statements_pkey')
    )
    op.create_index(op.f('ix_statements_account_id'), 'statements', ['account_id'], unique=False)
    op.create_index(op.f('ix_statements_period_end'), 'statements', ['period_end'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_statements_period_end'), table_name='statements')
    op.drop_index(op.f('ix_statements_account_id'), table_name='statements')
    op.drop_table('statements')
