"""volunteer cancellation of published assignments

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-16

Additive: a new table. Published rows (schedule_versions, ambulance_slots, assignments) are not changed.
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'assignment_cancellations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version_id', sa.Uuid(), nullable=False),
        sa.Column('shift_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['version_id'], ['schedule_versions.id'],
                                name=op.f('fk_assignment_cancellations_version_id_schedule_versions')),
        sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_assignment_cancellations_shift_id_shifts')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_assignment_cancellations_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_assignment_cancellations')),
        sa.UniqueConstraint('version_id', 'shift_id', 'user_id',
                            name=op.f('uq_assignment_cancellations_version_id_shift_id_user_id')),
    )
    op.create_index(op.f('ix_assignment_cancellations_version_id'), 'assignment_cancellations', ['version_id'],
                    unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_assignment_cancellations_version_id'), table_name='assignment_cancellations')
    op.drop_table('assignment_cancellations')
