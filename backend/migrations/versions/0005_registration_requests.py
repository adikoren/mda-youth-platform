"""registration requests to the day manager

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16

Additive only: a new table. No existing registration, choice, assignment or history is changed.
"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'registration_requests',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('activity_day_id', sa.Uuid(), nullable=False),
        sa.Column('message', sa.String(length=1000), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by_id', sa.Uuid(), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'resolved')", name=op.f('ck_registration_requests_status')),
        sa.ForeignKeyConstraint(['activity_day_id'], ['activity_days.id'],
                                name=op.f('fk_registration_requests_activity_day_id_activity_days')),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'],
                                name=op.f('fk_registration_requests_resolved_by_id_users')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_registration_requests_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_registration_requests')),
    )
    op.create_index(op.f('ix_registration_requests_user_id'), 'registration_requests', ['user_id'], unique=False)
    op.create_index(op.f('ix_registration_requests_activity_day_id'), 'registration_requests',
                    ['activity_day_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_registration_requests_activity_day_id'), table_name='registration_requests')
    op.drop_index(op.f('ix_registration_requests_user_id'), table_name='registration_requests')
    op.drop_table('registration_requests')
