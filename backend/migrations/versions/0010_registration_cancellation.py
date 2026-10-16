"""explicit cancellation state on registration choices

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-16

Additive. The original answer (`available`) and its timestamps are kept; `cancelled_at` / `cancelled_by_id`
record that the volunteer cancelled it, and when and by whom. Cancelled choices are excluded from candidates.
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('registration_choices', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('registration_choices', sa.Column('cancelled_by_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_registration_choices_cancelled_by_id_users'), 'registration_choices', 'users',
                          ['cancelled_by_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(op.f('fk_registration_choices_cancelled_by_id_users'), 'registration_choices', type_='foreignkey')
    op.drop_column('registration_choices', 'cancelled_by_id')
    op.drop_column('registration_choices', 'cancelled_at')
