from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.timeutil import local_midnight_after, to_local
from app.days import service as days
from app.days.models import ActivityDay, Shift
from tests.conftest import Client, at

# Approved table (design §2). Activity dates in the week of Sun 18 Oct 2026.
EXPECTED = {
    "2026-10-18": ("Tue 13 20:00", "Fri 16 18:00", "Sat 17 20:00"),  # Sunday
    "2026-10-19": ("Wed 14 20:00", "Sat 17 18:00", "Sun 18 20:00"),  # Monday
    "2026-10-20": ("Thu 15 20:00", "Sun 18 18:00", "Mon 19 20:00"),  # Tuesday
    "2026-10-21": ("Fri 16 14:00", "Mon 19 18:00", "Tue 20 20:00"),  # Wednesday: Friday 14:00 exception
    "2026-10-22": ("Sat 17 20:00", "Tue 20 18:00", "Wed 21 20:00"),  # Thursday
    "2026-10-23": ("Sun 18 20:00", "Wed 21 18:00", "Thu 22 20:00"),  # Friday
    "2026-10-24": ("Mon 19 20:00", "Thu 22 18:00", "Fri 23 20:00"),  # Saturday
}


def fmt(instant: datetime) -> str:
    return to_local(instant).strftime("%a %d %H:%M")


def test_all_seven_templates_match_approved_table(world, db):
    for d, (opens, closes, target) in EXPECTED.items():
        day = db.scalar(select(ActivityDay).where(ActivityDay.local_date == date.fromisoformat(d)))
        assert (fmt(day.registration_opens_at), fmt(day.registration_closes_at), fmt(day.publish_target_at)) == \
               (opens, closes, target), d


def test_special_days_have_morning_and_evening_selectable_and_no_reinforcement(world, db):
    for d in ("2026-10-23", "2026-10-24"):
        shifts = {s.kind: s for s in db.scalars(select(Shift).where(Shift.activity_day_id == world.days[d].id))}
        assert set(shifts) == {"morning", "evening"}
        assert not any(s.allows_late_arrival for s in shifts.values())
        assert shifts["evening"].self_selectable
    # Morning registration: Saturday open by default; Friday hidden until a manager opens it (2026-10-12).
    morning = lambda d: next(s for s in db.scalars(select(Shift).where(Shift.activity_day_id == world.days[d].id))
                             if s.kind == "morning")
    assert morning("2026-10-24").self_selectable and not morning("2026-10-23").self_selectable
    ordinary = {s.kind: s for s in db.scalars(select(Shift).where(Shift.activity_day_id == world.days["2026-10-22"].id))}
    assert set(ordinary) == {"morning", "reinforcement", "evening"}
    assert [k for k, s in ordinary.items() if s.self_selectable] == ["evening"]
    assert ordinary["evening"].allows_late_arrival


def test_holiday_classification_switches_shift_structure_before_use(world, db, clock):
    tue = world.days["2026-10-27"]
    days.change_kind(db, tue, "holiday", world.users["admin"].id, clock.now())
    db.commit()
    kinds = {s.kind for s in db.scalars(select(Shift).where(Shift.activity_day_id == tue.id))}
    assert kinds == {"morning", "evening"}


def test_template_edit_does_not_change_existing_dates(world, db, clock):
    thu = world.days["2026-10-22"]
    before = thu.registration_opens_at
    admin = Client("admin@x.test")
    r = admin.put("/api/templates/4", {"open_time": "19:00"})
    assert r.status_code == 200 and r.json()["open_time"] == "19:00"
    db.refresh(thu)
    assert thu.registration_opens_at == before
    new_day = days.create_day(db, date(2026, 11, 26), clock.now())
    assert fmt(new_day.registration_opens_at) == "Sat 21 19:00"


def test_dst_end_boundaries_are_built_in_local_time(world):
    # Israel leaves DST on Sun 25 Oct 2026 02:00. Midnight after Sat 24 is still IDT (UTC+3);
    # 10:00 on Sun 25 is IST (UTC+2). Naive "+N hours" arithmetic would be an hour off.
    assert local_midnight_after(date(2026, 10, 24)) == datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc)
    assert local_midnight_after(date(2026, 10, 25)) == datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc)
    assert at("2026-10-25", "10:00") == datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc)
    assert at("2026-10-25", "10:00") - at("2026-10-24", "23:00") == timedelta(hours=12)


def test_day_timing_edit_requires_scope(world, clock):
    thu = world.day("2026-10-22")
    mon = Client("mon.vol@x.test")
    assert mon.patch(f"/api/days/{thu}/timing", {"publish_target_at": "2026-10-21T15:00:00Z"}).status_code == 404
    mgr = Client("thu.mgr@x.test")
    r = mgr.patch(f"/api/days/{thu}/timing", {"publish_target_at": "2026-10-21T15:00:00Z"})
    assert r.status_code == 200
    other = mgr.patch(f"/api/days/{world.day('2026-10-19')}/timing", {"publish_target_at": "2026-10-18T15:00:00Z"})
    assert other.status_code == 404


def test_kind_change_refused_once_date_is_in_use(world, db, clock):
    thu = world.days["2026-10-22"]
    mgr = Client("thu.mgr@x.test")
    shifts = {s.kind: s for s in db.scalars(select(Shift).where(Shift.activity_day_id == thu.id))}
    assert mgr.post(f"/api/manage/shifts/{shifts['morning'].id}/participants",
                    {"user_id": world.uid("thu_youth")}).status_code == 201
    r = mgr.patch(f"/api/days/{thu.id}/kind", {"kind": "holiday"})
    assert r.status_code == 409 and r.json()["code"] == "day_in_use"
