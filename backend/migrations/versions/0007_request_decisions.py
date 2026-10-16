"""request decisions: approved / rejected

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16

Widens the status check. Existing rows keep their values ('pending' or the legacy 'resolved').
"""
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

OLD = "status IN ('pending', 'resolved')"
NEW = "status IN ('pending', 'approved', 'rejected', 'resolved')"


def upgrade() -> None:
    op.drop_constraint(op.f('ck_registration_requests_status'), 'registration_requests', type_='check')
    op.create_check_constraint(op.f('ck_registration_requests_status'), 'registration_requests', NEW)


def downgrade() -> None:
    op.execute("UPDATE registration_requests SET status = 'resolved' WHERE status IN ('approved', 'rejected')")
    op.drop_constraint(op.f('ck_registration_requests_status'), 'registration_requests', type_='check')
    op.create_check_constraint(op.f('ck_registration_requests_status'), 'registration_requests', OLD)
