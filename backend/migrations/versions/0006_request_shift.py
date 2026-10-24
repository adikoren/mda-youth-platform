"""registration requests per shift

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16

Additive: a nullable shift reference. Existing requests keep null (day-level, from before per-shift
requests); new requests always name their shift. Nothing else is changed.
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('registration_requests', sa.Column('shift_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(op.f('fk_registration_requests_shift_id_shifts'), 'registration_requests', 'shifts',
                          ['shift_id'], ['id'])
    op.create_index(op.f('ix_registration_requests_shift_id'), 'registration_requests', ['shift_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_registration_requests_shift_id'), table_name='registration_requests')
    op.drop_constraint(op.f('fk_registration_requests_shift_id_shifts'), 'registration_requests', type_='foreignkey')
    op.drop_column('registration_requests', 'shift_id')
