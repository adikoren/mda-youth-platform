"""Schedule retention (decision 2026-10-16): a date's published schedule is kept for two months after its last
shift ends, then its schedule rows are deleted. Deleted: slots, assignments, cancellations and availability
attention rows of that date's versions. Kept: the date and its shifts, rides and ride assessments, mentoring
summaries and their corrections, trainee progress, notifications and the audit log. The version header stays
(marked purged_at), so activity_days keeps its publication link."""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.days.models import ActivityDay, Shift
from app.registration.models import AvailabilityAttention
from app.scheduling.models import AmbulanceSlot, Assignment, AssignmentCancellation, ScheduleVersion

RETENTION = timedelta(days=60)  # "two months" after the last shift of the date ends


def expired_day_ids(db: Session, now: datetime) -> list[uuid.UUID]:
    cutoff = now - RETENTION
    return list(db.scalars(
        select(ActivityDay.id)
        .join(Shift, Shift.activity_day_id == ActivityDay.id)
        .group_by(ActivityDay.id)
        .having(func.max(Shift.ends_at) < cutoff)))


def purge_expired(db: Session, now: datetime) -> dict:
    """Delete the schedule rows of every date whose last shift ended more than RETENTION ago. Idempotent: a
    version already purged is skipped. Runs in one transaction; the immutability trigger is bypassed only
    inside it (set_config with is_local = true)."""
    day_ids = expired_day_ids(db, now)
    version_ids = list(db.scalars(select(ScheduleVersion.id).where(
        ScheduleVersion.activity_day_id.in_(day_ids), ScheduleVersion.purged_at.is_(None)))) if day_ids else []
    if not version_ids:
        db.rollback()
        return {"versions": 0, "assignments": 0, "slots": 0, "cancellations": 0, "attention": 0}
    db.execute(text("SELECT set_config('mda.purge', 'on', true)"))
    attention = db.execute(delete(AvailabilityAttention).where(
        AvailabilityAttention.published_version_id.in_(version_ids))).rowcount
    cancellations = db.execute(delete(AssignmentCancellation).where(
        AssignmentCancellation.version_id.in_(version_ids))).rowcount
    assignments = db.execute(delete(Assignment).where(Assignment.version_id.in_(version_ids))).rowcount
    slots = db.execute(delete(AmbulanceSlot).where(AmbulanceSlot.version_id.in_(version_ids))).rowcount
    db.execute(update(ScheduleVersion).where(ScheduleVersion.id.in_(version_ids)).values(purged_at=now))
    db.commit()
    return {"versions": len(version_ids), "assignments": assignments, "slots": slots,
            "cancellations": cancellations, "attention": attention}
