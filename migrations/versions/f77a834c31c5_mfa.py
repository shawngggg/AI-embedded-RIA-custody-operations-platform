"""Second factor for password sign-in: the TOTP secret (sealed), when it was turned on, the last step used

Revision ID: f77a834c31c5
Revises: 8b7e19c73c8e
Create Date: 2026-10-25 22:19:36.402572

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f77a834c31c5'
down_revision: Union[str, Sequence[str], None] = '8b7e19c73c8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('mfa_secret', sa.String(length=200), nullable=True))
    op.add_column('users', sa.Column('mfa_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('mfa_last_step', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'mfa_last_step')
    op.drop_column('users', 'mfa_enabled_at')
    op.drop_column('users', 'mfa_secret')
