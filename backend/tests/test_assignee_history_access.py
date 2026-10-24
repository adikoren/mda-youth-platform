"""Regression (decision 2026-10-16): a volunteer assigned to a non-home day keeps access to that date's published
schedule after the temporary access would have ended, and it stays in their history. Access is granted by the
recorded assignment to that date only, never to other dates on the same weekday, and retention ends it."""
from datetime import timedelta

from tests.conftest import Client, at
from tests.test_journal import NEXT_THU, THU, publish_evening

VOL = "mon_trainee"  # home day Monday, assigned on Thursday


def setup_non_home_assignment(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU, slot1=("thu_mentor", "thu_mgr", VOL))
    publish_evening(world, NEXT_THU)  # the same weekday, but this volunteer is not assigned there


def test_non_home_assignment_survives_temporary_access_expiry_and_stays_in_history(world, clock):
    setup_non_home_assignment(world, clock)
    vol = Client("mon.trainee@x.test")
    clock.set(at(THU, "18:00"))
    assert vol.get(f"/api/schedule/{world.day(THU)}").status_code == 200
    # Well after the day: the invitation/temporary window is gone, the assignment is not.
    clock.set(at("2026-10-17", "09:00"))
    assert vol.get(f"/api/schedule/{world.day(THU)}").status_code == 200
    items = {d["date"]: d for d in vol.get("/api/schedule").json()["items"]}
    assert THU in items and items[THU]["historical"] is True
    assert [h["date"] for h in vol.get("/api/me/history").json()] == [THU]


def test_no_access_to_other_dates_on_the_same_weekday(world, clock):
    setup_non_home_assignment(world, clock)
    vol = Client("mon.trainee@x.test")
    clock.set(at("2026-10-17", "09:00"))
    assert vol.get(f"/api/schedule/{world.day(NEXT_THU)}").status_code == 404
    assert NEXT_THU not in {d["date"] for d in vol.get("/api/schedule").json()["items"]}


def test_retention_ends_the_historical_access_with_the_rows(world, db, clock):
    from app.scheduling.retention import purge_expired
    from app.days.models import ActivityDay, Shift
    from sqlalchemy import func, select
    from datetime import date
    setup_non_home_assignment(world, clock)
    vol = Client("mon.trainee@x.test")
    ends = db.scalar(select(func.max(Shift.ends_at)).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                     .where(ActivityDay.local_date == date.fromisoformat(THU)))
    clock.set(ends + timedelta(days=61))
    purge_expired(db, clock.now())
    vol = Client("mon.trainee@x.test")  # a fresh login: the earlier session is long past its inactivity limit
    assert vol.get(f"/api/schedule/{world.day(THU)}").status_code == 404
    assert vol.get("/api/me/history").json() == []
