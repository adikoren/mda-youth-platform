"""Web Push subscriptions (one row per device, belonging to one account)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-17

Additive: a new table. Endpoints and keys are stored for sending only and are never returned by the API.
"""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('endpoint', sa.String(length=1024), nullable=False),
        sa.Column('p256dh', sa.String(length=200), nullable=False),
        sa.Column('auth', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_push_subscriptions_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_push_subscriptions')),
        sa.UniqueConstraint('endpoint', name=op.f('uq_push_subscriptions_endpoint')),
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
