"""Schedule retention (decision 2026-10-16): two months after a date's last shift ends, its schedule rows are
deleted. Journal records, ride counts, mentoring summaries, trainee progress, the date and its shifts are kept,
and the immutability guard still applies to every ordinary write."""
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text

from app.audit.models import AuditEvent
from app.days.models import ActivityDay, Shift
from app.mentoring.models import MentoringSummary, Ride, RideAssessment
from app.scheduling.models import AmbulanceSlot, Assignment, ScheduleVersion
from app.scheduling.retention import RETENTION, purge_expired
from tests.conftest import Client, at
from tests.test_journal import FULL, THU, new_ride, publish_evening


def last_shift_end(db):
    return db.scalar(select(func.max(Shift.ends_at)).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                     .where(ActivityDay.local_date == date.fromisoformat(THU)))


def counts(db) -> dict:
    db.expire_all()
    return {
        "rides": db.scalar(select(func.count()).select_from(Ride)),
        "assessments": db.scalar(select(func.count()).select_from(RideAssessment)),
        "summaries": db.scalar(select(func.count()).select_from(MentoringSummary)),
        "shifts": db.scalar(select(func.count()).select_from(Shift)),
        "days": db.scalar(select(func.count()).select_from(ActivityDay)),
        "audit": db.scalar(select(func.count()).select_from(AuditEvent)),
    }


def test_purge_removes_schedule_rows_and_keeps_journal_progress_and_history(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    shift_id = publish_evening(world, THU)
    clock.set(at(THU, "18:00"))  # rides can be recorded on the shift day or later
    trainee = Client("thu.trainee@x.test")
    rid, r = new_ride(trainee, world, shift_id=shift_id, description="סחרחורת")
    assert r.status_code == 201, r.text
    assert Client("thu.mentor@x.test").put(f"/api/journal/rides/{rid}/assessment",
                                           {"markers": FULL, "summary": "טיפול טוב", "expected_version": 0}).status_code == 200
    before = counts(db)
    assert before["rides"] == 1 and before["assessments"] == 1

    day_row = db.scalar(select(ActivityDay).where(ActivityDay.local_date == date.fromisoformat(THU)))
    version_id = day_row.latest_published_version_id
    assert version_id is not None
    assert db.scalar(select(func.count()).select_from(Assignment).where(Assignment.version_id == version_id)) > 0

    # Not yet expired (one day before the retention limit): nothing is deleted.
    clock.set(last_shift_end(db) + RETENTION - timedelta(days=1))
    assert purge_expired(db, clock.now())["versions"] == 0

    # Expired: schedule rows go, journal and history stay.
    clock.set(last_shift_end(db) + RETENTION + timedelta(hours=1))
    result = purge_expired(db, clock.now())
    assert result["versions"] == 1 and result["assignments"] > 0 and result["slots"] > 0
    after = counts(db)
    assert after == before  # rides, assessments, summaries, shifts, dates and the audit log are unchanged
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Assignment).where(Assignment.version_id == version_id)) == 0
    assert db.scalar(select(func.count()).select_from(AmbulanceSlot).where(AmbulanceSlot.version_id == version_id)) == 0
    header = db.get(ScheduleVersion, version_id)
    assert header is not None and header.purged_at is not None  # header kept; activity day still links to it
    assert db.get(ActivityDay, day_row.id).latest_published_version_id == version_id

    # Trainee progress still counts the ride.
    assert Client("thu.trainee@x.test").get("/api/journal/me").json()["progress"]["ride_count"] == 1

    # Idempotent: a second run finds nothing to delete.
    assert purge_expired(db, clock.now())["versions"] == 0


def test_immutability_still_blocks_ordinary_writes_to_published_rows(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU)
    with pytest.raises(Exception) as err:
        db.execute(text("delete from assignments where user_id = :u"), {"u": str(world.uid("thu_youth"))})
        db.flush()
    db.rollback()
    assert "immutable" in str(err.value)
