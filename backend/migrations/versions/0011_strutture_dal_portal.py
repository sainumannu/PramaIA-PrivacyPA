"""collegamento delle strutture organizzative all'organigramma del Portal

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('privacypa_organizational_units', schema=None) as batch_op:
        batch_op.add_column(sa.Column('portal_node_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_privacypa_organizational_units_portal_node_id'), ['portal_node_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('privacypa_organizational_units', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_privacypa_organizational_units_portal_node_id'))
        batch_op.drop_column('portal_node_id')
