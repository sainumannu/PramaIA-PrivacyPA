"""eventi esterni

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-21 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('privacypa_external_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('row_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('tenant_id', sa.String(length=100), nullable=False),
    sa.Column('source', sa.String(length=30), nullable=False),
    sa.Column('event_id', sa.String(length=100), nullable=False),
    sa.Column('event_type', sa.String(length=60), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('processing_activity_id', sa.String(length=36), nullable=True),
    sa.Column('claimed_processing_id', sa.String(length=64), nullable=True),
    sa.Column('subject_ref', sa.String(length=100), nullable=True),
    sa.Column('summary', sa.String(length=200), nullable=True),
    sa.Column('link', sa.String(length=500), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'UNMATCHED', 'ARCHIVED', name='externaleventstatus', native_enum=False, length=20), nullable=False),
    sa.Column('received_by', sa.String(length=100), nullable=True),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.Column('archived_by', sa.String(length=100), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('archive_note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'source', 'event_id', name='uix_privacypa_external_event')
    )
    with op.batch_alter_table('privacypa_external_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_privacypa_external_events_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_privacypa_external_events_processing_activity_id'), ['processing_activity_id'], unique=False)
        batch_op.create_index('ix_privacypa_external_event_pending', ['tenant_id', 'status', 'processing_activity_id'], unique=False)


def downgrade() -> None:
    op.drop_table('privacypa_external_events')
