"""assegnazioni di ruolo modificabili: row_version per l'If-Match

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-02 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('privacypa_user_role_assignments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('row_version', sa.Integer(), server_default='1', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('privacypa_user_role_assignments', schema=None) as batch_op:
        batch_op.drop_column('row_version')
