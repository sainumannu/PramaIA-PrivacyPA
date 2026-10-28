"""richieste di accesso

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27 15:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('privacypa_access_assessments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=100), nullable=False),
    sa.Column('source_module', sa.String(length=30), nullable=False),
    sa.Column('source_ref', sa.String(length=100), nullable=False),
    sa.Column('request_version', sa.Integer(), nullable=False),
    sa.Column('request_type', sa.String(length=10), nullable=False),
    sa.Column('subject_user_id', sa.String(length=100), nullable=True),
    sa.Column('asset_id', sa.String(length=36), nullable=True),
    sa.Column('profile_id', sa.String(length=36), nullable=True),
    sa.Column('profile_version', sa.Integer(), nullable=True),
    sa.Column('current_authorization_id', sa.String(length=36), nullable=True),
    sa.Column('request', sa.JSON(), nullable=True),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('checks', sa.JSON(), nullable=True),
    sa.Column('organizational_unit_id', sa.String(length=36), nullable=True),
    sa.Column('requested_by', sa.String(length=100), nullable=False),
    sa.Column('requested_on_behalf_of', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'source_module', 'source_ref', 'request_version', name='uix_privacypa_access_assessment_source')
    )
    with op.batch_alter_table('privacypa_access_assessments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_privacypa_access_assessments_organizational_unit_id'), ['organizational_unit_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_privacypa_access_assessments_tenant_id'), ['tenant_id'], unique=False)

    with op.batch_alter_table('privacypa_approvals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('requested_on_behalf_of', sa.String(length=100), nullable=True))

    with op.batch_alter_table('privacypa_assets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('data_owner_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('data_owner_role', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('data_owner_user_id', sa.String(length=100), nullable=True))

    with op.batch_alter_table('privacypa_authorization_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('asset_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('data_category_ids', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('allowed_operations', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('allowed_scope_types', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('processing_activity_ids', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('required_training_course_ids', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('max_duration_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False))
        batch_op.create_index(batch_op.f('ix_privacypa_authorization_profiles_asset_id'), ['asset_id'], unique=False)
        batch_op.create_index('uix_privacypa_auth_profile_code', ['tenant_id', 'code'], unique=True)
        batch_op.create_foreign_key('fk_privacypa_auth_profile_asset', 'privacypa_assets', ['asset_id'], ['id'])

    with op.batch_alter_table('privacypa_authorizations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('authorized_by_name', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('authorization_reference', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('authorization_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('authorization_notes', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('operations', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('scope', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('profile_version', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('source_module', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('source_ref', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('forced', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        batch_op.add_column(sa.Column('revocation_reference', sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_privacypa_authorizations_source_ref'), ['source_ref'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('privacypa_authorizations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_privacypa_authorizations_source_ref'))
        batch_op.drop_column('revocation_reference')
        batch_op.drop_column('forced')
        batch_op.drop_column('source_ref')
        batch_op.drop_column('source_module')
        batch_op.drop_column('profile_version')
        batch_op.drop_column('scope')
        batch_op.drop_column('operations')
        batch_op.drop_column('authorization_notes')
        batch_op.drop_column('authorization_date')
        batch_op.drop_column('authorization_reference')
        batch_op.drop_column('authorized_by_name')

    with op.batch_alter_table('privacypa_authorization_profiles', schema=None) as batch_op:
        batch_op.drop_constraint('fk_privacypa_auth_profile_asset', type_='foreignkey')
        batch_op.drop_index('uix_privacypa_auth_profile_code')
        batch_op.drop_index(batch_op.f('ix_privacypa_authorization_profiles_asset_id'))
        batch_op.drop_column('active')
        batch_op.drop_column('max_duration_days')
        batch_op.drop_column('required_training_course_ids')
        batch_op.drop_column('processing_activity_ids')
        batch_op.drop_column('allowed_scope_types')
        batch_op.drop_column('allowed_operations')
        batch_op.drop_column('data_category_ids')
        batch_op.drop_column('asset_id')
        batch_op.drop_column('code')

    with op.batch_alter_table('privacypa_assets', schema=None) as batch_op:
        batch_op.drop_column('data_owner_user_id')
        batch_op.drop_column('data_owner_role')
        batch_op.drop_column('data_owner_name')

    with op.batch_alter_table('privacypa_approvals', schema=None) as batch_op:
        batch_op.drop_column('requested_on_behalf_of')

    with op.batch_alter_table('privacypa_access_assessments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_privacypa_access_assessments_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_privacypa_access_assessments_organizational_unit_id'))

    op.drop_table('privacypa_access_assessments')
