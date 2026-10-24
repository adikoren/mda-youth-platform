"""Shift types per date (decision 2026-10-16). Evening is mandatory and the default; morning and reinforcement are
optional and explicit; a date never has morning or reinforcement without evening; reinforcement only on ordinary
days; the spec ambulance defaults are kept. A type added later never reopens registration or joining, keeps the
published schedule, and notifies the date's home-day volunteers."""
from datetime import date

import pytest
from sqlalchemy import select

from app.days import service as days
from app.core.errors import AppError
from app.days.models import ActivityDay, Shift
from app.notifications.models import OutboxMessage
from app.scheduling.models import AmbulanceSlot, Assignment
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, register, shift

MON_NEW = date(2026, 11, 2)  # Monday, not in the shared fixture window
FRI_NEW = date(2026, 11, 6)  # Friday (special day)
TUE_NEW = date(2026, 11, 3)


def kinds(db, d: date) -> dict[str, int]:
    rows = db.execute(select(Shift.kind, Shift.baseline_slots).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                      .where(ActivityDay.local_date == d)).all()
    return {k: b for k, b in rows}


def new_day(db, clock, d: date, **kw):
    day = days.create_day(db, d, clock.now(), **kw)
    db.commit()
    return day


def test_a_new_date_has_evening_only_by_default(world, db, clock):
    new_day(db, clock, TUE_NEW)
    assert kinds(db, TUE_NEW) == {"evening": 2}


def test_optional_types_are_created_only_when_selected_with_spec_ambulance_defaults(world, db, clock):
    new_day(db, clock, MON_NEW, shift_kinds=("morning", "reinforcement", "evening"))
    assert kinds(db, MON_NEW) == {"morning": 3, "reinforcement": 1, "evening": 2}  # ordinary-day baseline


def test_special_day_defaults_are_kept(world, db, clock):
    new_day(db, clock, FRI_NEW, kind="friday", shift_kinds=("morning", "evening"))
    assert kinds(db, FRI_NEW) == {"morning": 2, "evening": 2}


def test_evening_is_mandatory(world, db, clock):
    with pytest.raises(AppError) as err:
        days.create_day(db, MON_NEW, clock.now(), shift_kinds=("morning",))
    assert err.value.code == "evening_required"
    db.rollback()


def test_reinforcement_is_refused_on_special_days(world, db, clock):
    with pytest.raises(AppError) as err:
        days.create_day(db, FRI_NEW, clock.now(), kind="friday", shift_kinds=("reinforcement", "evening"))
    assert err.value.code == "shift_not_allowed"
    db.rollback()


def test_adding_a_type_later_does_not_reopen_registration_and_keeps_the_published_schedule(world, db, clock):
    admin = Client("admin@x.test")
    clock.set(at("2026-10-12", "21:00"))
    day = new_day(db, clock, MON_NEW)
    ev = admin.get(f"/api/manage/days/{day.id}/board").json()
    assert [s["kind"] for s in ev["shifts"]] == ["evening"]  # only evening: no empty morning section
    eve = shift(board(admin, str(day.id)), "evening")
    assign(admin, eve["slots"][0]["slot_key"], world.uid("mon_vol"))
    assert publish(admin, str(day.id)).status_code == 200
    crew_before = Client("mon.vol@x.test").get(f"/api/schedule/{day.id}").json()

    r = admin.post(f"/api/manage/days/{day.id}/shifts", {"kind": "morning"})
    assert r.status_code == 201, r.text
    assert admin.post(f"/api/manage/days/{day.id}/shifts", {"kind": "morning"}).status_code == 409  # exists already

    # The published schedule is unchanged (the new type has no published ambulance).
    assert Client("mon.vol@x.test").get(f"/api/schedule/{day.id}").json()["shifts"] == crew_before["shifts"]
    # Volunteers cannot join the new type: it is not selectable and no request is accepted after publication.
    vol = Client("mon.trainee@x.test")
    morning = db.scalar(select(Shift.id).where(Shift.activity_day_id == day.id, Shift.kind == "morning"))
    r = vol.put(f"/api/registrations/{day.id}", {"choices": [{"shift_id": str(morning), "available": True}]})
    assert r.status_code >= 400
    assert all(s["kind"] != "morning" for s in vol.get(f"/api/registrations/{day.id}").json()["shifts"])
    # The manager assigns directly on the working copy, with the spec baseline of 3 ambulances for morning.
    mrn = next(s for s in board(admin, str(day.id))["shifts"] if s["kind"] == "morning")  # opens the working copy
    assert len(mrn["slots"]) == 3


def test_adding_a_type_notifies_the_home_day_volunteers(world, db, clock):
    admin = Client("admin@x.test")
    clock.set(at("2026-10-12", "21:00"))
    day = new_day(db, clock, MON_NEW)
    assert admin.post(f"/api/manage/days/{day.id}/shifts", {"kind": "reinforcement"}).status_code == 201
    msgs = db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "shift_type_added")).all()
    recipients = {m.recipient_user_id for m in msgs}
    assert world.users["mon_vol"].id in recipients and world.users["mon_trainee"].id in recipients
    assert world.users["thu_youth"].id not in recipients  # other home days are not notified


def test_guest_volunteer_shows_actual_home_weekday(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    from tests.test_journal import THU, publish_evening
    publish_evening(world, THU, slot1=("thu_mentor", "thu_mgr", "mon_trainee"))
    crew = Client("thu.mgr@x.test").get(f"/api/schedule/{world.day(THU)}").json()
    person = next(p for sh in crew["shifts"] for sl in sh["slots"] for p in sl["crew"]
                  if p["user_id"] == str(world.uid("mon_trainee")))
    assert person["is_guest"] is True and person["home_weekday_name"] == "שני"


def test_profile_visibility_follows_existing_permissions(world, db, clock):
    mon = str(world.uid("mon_vol"))
    assert Client("mon.vol@x.test").get(f"/api/people/{mon}").status_code == 200  # self
    assert Client("admin@x.test").get(f"/api/people/{mon}").status_code == 200
    assert Client("thu.trainee@x.test").get(f"/api/people/{mon}").status_code == 404  # no relation
    assert Client("thu.mgr@x.test").get(f"/api/people/{mon}").status_code == 404  # not on a date they manage
    assert Client("mon.vol@x.test").get(f"/api/people/{world.uid('thu_youth')}").status_code == 404
