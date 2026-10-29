"""password reset tokens, Google identities, OAuth attempt states

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_resets")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_resets_token_hash")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_password_resets_user_id_users")),
    )
    op.create_index(op.f("ix_password_resets_user_id"), "password_resets", ["user_id"])
    op.create_table(
        "external_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_identities")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_external_identities_user_id_users")),
    )
    op.create_index(op.f("ix_external_identities_user_id"), "external_identities", ["user_id"])
    op.create_index("uq_external_identity", "external_identities", ["provider", "subject"], unique=True)
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_oauth_states_state_hash")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_oauth_states_user_id_users")),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
    op.drop_index("uq_external_identity", table_name="external_identities")
    op.drop_table("external_identities")
    op.drop_table("password_resets")
