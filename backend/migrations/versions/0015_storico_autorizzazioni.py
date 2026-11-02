"""storico delle autorizzazioni: una versione per ogni concessione, modifica, revoca o conferma

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-02 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('privacypa_authorization_snapshots',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=100), nullable=False),
    sa.Column('authorization_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('actor_user_id', sa.String(length=100), nullable=True),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['authorization_id'], ['privacypa_authorizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('privacypa_authorization_snapshots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_privacypa_authorization_snapshots_authorization_id'), ['authorization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_privacypa_authorization_snapshots_tenant_id'), ['tenant_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('privacypa_authorization_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_privacypa_authorization_snapshots_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_privacypa_authorization_snapshots_authorization_id'))
    op.drop_table('privacypa_authorization_snapshots')
