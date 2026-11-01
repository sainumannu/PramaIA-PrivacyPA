"""archivi cartacei: sede, locale, fase, consistenza, custode e rischi ambientali dell'asset

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-01 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('privacypa_assets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location_site', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('location_room', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('archive_phase', sa.Enum('CURRENT', 'DEPOSIT', 'HISTORICAL', name='archivephase',
                                                               native_enum=False, length=20), nullable=True))
        batch_op.add_column(sa.Column('archive_extent', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('custodian_user_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('environmental_risks', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('privacypa_assets', schema=None) as batch_op:
        batch_op.drop_column('environmental_risks')
        batch_op.drop_column('custodian_user_id')
        batch_op.drop_column('archive_extent')
        batch_op.drop_column('archive_phase')
        batch_op.drop_column('location_room')
        batch_op.drop_column('location_site')
