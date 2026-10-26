"""Regulation S-P incidents: assessment, containment, the notice determination, and the notices sent

Revision ID: 1ce0379d44cb
Revises: f77a834c31c5
Create Date: 2026-10-25 22:21:30.981384

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ce0379d44cb'
down_revision: Union[str, Sequence[str], None] = 'f77a834c31c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('privacy_incidents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=20), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('aware_on', sa.Date(), nullable=False),
    sa.Column('reported_by', sa.String(length=120), nullable=False),
    sa.Column('service_provider', sa.String(length=200), nullable=True),
    sa.Column('provider_aware_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('accounts', sa.JSON(), nullable=False),
    sa.Column('information', sa.Text(), nullable=True),
    sa.Column('contained_on', sa.Date(), nullable=True),
    sa.Column('containment', sa.Text(), nullable=True),
    sa.Column('determination', sa.String(length=20), nullable=True),
    sa.Column('determination_reason', sa.Text(), nullable=True),
    sa.Column('determined_by', sa.String(length=120), nullable=True),
    sa.Column('notices_sent_on', sa.Date(), nullable=True),
    sa.Column('notices_sent', sa.Integer(), nullable=False),
    sa.Column('risk_event_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['risk_event_id'], ['ops_risk_events.id'], name='privacy_incidents_risk_event_id_fkey'),
    sa.PrimaryKeyConstraint('id', name='privacy_incidents_pkey'),
    sa.UniqueConstraint('ref', name='privacy_incidents_ref_key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('privacy_incidents')
