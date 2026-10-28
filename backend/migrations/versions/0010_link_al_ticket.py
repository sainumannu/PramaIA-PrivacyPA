"""indirizzo del ticket nelle valutazioni delle richieste di accesso

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-27 19:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('privacypa_access_assessments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('privacypa_access_assessments', schema=None) as batch_op:
        batch_op.drop_column('source_url')
