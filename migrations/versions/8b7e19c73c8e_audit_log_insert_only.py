"""Audit log insert-only: a trigger refuses UPDATE, DELETE, and TRUNCATE on audit_events (app/audit_guard.py)

Revision ID: 8b7e19c73c8e
Revises: dfde31c129be
Create Date: 2026-10-25 22:15:30.990491

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b7e19c73c8e'
down_revision: Union[str, Sequence[str], None] = 'dfde31c129be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    from app.audit_guard import install
    install(op.get_bind())


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events")
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS audit_events_insert_only()")
