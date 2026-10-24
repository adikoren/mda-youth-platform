"""boarding order (סדר עלייה) and operating volunteers on additional ambulances

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16

Additive. Ambulance slots get optional operator fields; boarding_entries is a new per-version table.
Published rows keep their values; the working copy is copied from the base version by the application.
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ambulance_slots', sa.Column('operator_user_id', sa.Uuid(), nullable=True))
    op.add_column('ambulance_slots', sa.Column('operator_start', sa.Time(), nullable=True))
    op.add_column('ambulance_slots', sa.Column('operator_end', sa.Time(), nullable=True))
    op.create_foreign_key(op.f('fk_ambulance_slots_operator_user_id_users'), 'ambulance_slots', 'users',
                          ['operator_user_id'], ['id'])
    op.create_table(
        'boarding_entries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('version_id', sa.Uuid(), nullable=False),
        sa.Column('shift_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(['version_id'], ['schedule_versions.id'],
                                name=op.f('fk_boarding_entries_version_id_schedule_versions')),
        sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], name=op.f('fk_boarding_entries_shift_id_shifts')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_boarding_entries_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_boarding_entries')),
        sa.UniqueConstraint('version_id', 'user_id', name=op.f('uq_boarding_entries_version_id_user_id')),
    )
    op.create_index(op.f('ix_boarding_entries_version_id'), 'boarding_entries', ['version_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_boarding_entries_version_id'), table_name='boarding_entries')
    op.drop_table('boarding_entries')
    op.drop_constraint(op.f('fk_ambulance_slots_operator_user_id_users'), 'ambulance_slots', type_='foreignkey')
    op.drop_column('ambulance_slots', 'operator_end')
    op.drop_column('ambulance_slots', 'operator_start')
    op.drop_column('ambulance_slots', 'operator_user_id')
