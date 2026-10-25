"""integrazioni

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-21 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('privacypa_tenant_policies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('integrations', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('privacypa_tenant_policies', schema=None) as batch_op:
        batch_op.drop_column('integrations')
