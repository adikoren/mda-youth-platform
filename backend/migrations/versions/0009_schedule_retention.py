"""schedule retention: purge marker and a controlled purge bypass for the immutability trigger

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-16

Published schedule rows stay immutable for every ordinary write. The retention job (app.scheduling.retention)
sets `mda.purge` to 'on' for its own transaction only (set_config(..., is_local => true)), so the bypass
ends with that transaction. The version header row stays (activity_days keeps pointing at it); only its
slots, assignments, cancellations and availability attention rows are deleted, and purged_at is set.
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION enforce_draft_only() RETURNS trigger AS $$
DECLARE v_status text;
BEGIN
  IF current_setting('mda.purge', true) = 'on' THEN
    RETURN COALESCE(NEW, OLD);
  END IF;
  SELECT status INTO v_status FROM schedule_versions
   WHERE id = COALESCE(NEW.version_id, OLD.version_id);
  IF TG_OP = 'UPDATE' AND NEW.version_id <> OLD.version_id THEN
    RAISE EXCEPTION 'schedule rows cannot move between versions';
  END IF;
  IF v_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'schedule version % is % and immutable', COALESCE(NEW.version_id, OLD.version_id), v_status
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;
"""

_PREVIOUS_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION enforce_draft_only() RETURNS trigger AS $$
DECLARE v_status text;
BEGIN
  SELECT status INTO v_status FROM schedule_versions
   WHERE id = COALESCE(NEW.version_id, OLD.version_id);
  IF TG_OP = 'UPDATE' AND NEW.version_id <> OLD.version_id THEN
    RAISE EXCEPTION 'schedule rows cannot move between versions';
  END IF;
  IF v_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'schedule version % is % and immutable', COALESCE(NEW.version_id, OLD.version_id), v_status
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column('schedule_versions', sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(_TRIGGER_FN)


def downgrade() -> None:
    op.execute(_PREVIOUS_TRIGGER_FN)
    op.drop_column('schedule_versions', 'purged_at')
