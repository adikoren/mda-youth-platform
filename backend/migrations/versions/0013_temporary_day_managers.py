"""temporary day manager appointments; one permanent Day Manager scope per person and per weekday

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-17

Additive table for time-limited appointments. Two partial unique indexes make the permanent rules hold under
concurrency: a person has at most one active permanent weekday, and a weekday has at most one active permanent
Day Manager. Upgrade fails loudly if existing data already violates either rule.
"""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'temporary_day_manager_appointments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('weekday', sa.SmallInteger(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by_id', sa.Uuid(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by_id', sa.Uuid(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_id', sa.Uuid(), nullable=True),
        sa.CheckConstraint('weekday BETWEEN 0 AND 6', name=op.f('ck_temporary_day_manager_appointments_weekday')),
        sa.CheckConstraint('ends_at > starts_at', name=op.f('ck_temporary_day_manager_appointments_interval')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_temp_dm_user_id_users')),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_temp_dm_created_by_id_users')),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_temp_dm_updated_by_id_users')),
        sa.ForeignKeyConstraint(['revoked_by_id'], ['users.id'], name=op.f('fk_temp_dm_revoked_by_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_temporary_day_manager_appointments')),
    )
    op.create_index(op.f('ix_temporary_day_manager_appointments_user_id'), 'temporary_day_manager_appointments',
                    ['user_id'], unique=False)
    op.create_index('uq_one_permanent_scope_per_user', 'manager_day_scopes', ['user_id'], unique=True,
                    postgresql_where=sa.text('valid_to IS NULL'))
    op.create_index('uq_one_permanent_manager_per_weekday', 'manager_day_scopes', ['weekday'], unique=True,
                    postgresql_where=sa.text('valid_to IS NULL'))


def downgrade() -> None:
    op.drop_index('uq_one_permanent_manager_per_weekday', table_name='manager_day_scopes')
    op.drop_index('uq_one_permanent_scope_per_user', table_name='manager_day_scopes')
    op.drop_index(op.f('ix_temporary_day_manager_appointments_user_id'), table_name='temporary_day_manager_appointments')
    op.drop_table('temporary_day_manager_appointments')
