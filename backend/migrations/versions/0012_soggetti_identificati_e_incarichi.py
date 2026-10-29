"""soggetti esterni: persone fisiche e durata dell'incarico di responsabili e contitolari

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-30 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('privacypa_parties', schema=None) as batch_op:
        batch_op.add_column(sa.Column('natural_person', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('privacypa_processor_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('appointment_start', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('appointment_end', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('appointment_contract_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('ceased_on', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('cessation_note', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('privacypa_processor_profiles', schema=None) as batch_op:
        batch_op.drop_column('cessation_note')
        batch_op.drop_column('ceased_on')
        batch_op.drop_column('appointment_contract_id')
        batch_op.drop_column('appointment_end')
        batch_op.drop_column('appointment_start')
    with op.batch_alter_table('privacypa_parties', schema=None) as batch_op:
        batch_op.drop_column('natural_person')
