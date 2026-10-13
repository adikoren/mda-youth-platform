"""morning registration closed by default (Sunday–Friday)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12

Data-only. Sunday–Friday morning shifts become hidden from volunteer registration unless a manager
opens them. To preserve existing data, a morning shift that already has any registration answer
stays open; registrations, assignments and history are never modified.
"""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE shifts s
           SET self_selectable = false
          FROM activity_days d
         WHERE d.id = s.activity_day_id
           AND s.kind = 'morning'
           AND s.self_selectable
           AND EXTRACT(DOW FROM d.local_date) <> 6
           AND NOT EXISTS (SELECT 1 FROM registration_choices c WHERE c.shift_id = s.id)
    """)


def downgrade() -> None:
    # Restore the previous default: special-day (friday/holiday/holiday_eve) mornings self-selectable.
    op.execute("""
        UPDATE shifts s
           SET self_selectable = true
          FROM activity_days d
         WHERE d.id = s.activity_day_id
           AND s.kind = 'morning'
           AND d.kind IN ('friday', 'holiday', 'holiday_eve')
    """)
