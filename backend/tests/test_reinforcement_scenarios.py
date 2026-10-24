"""Reinforcement (decision of 2026-10-17): opening a shift and opening volunteer registration are separate.
1) Opened during setup: volunteers may select reinforcement alongside the other open options, no trainee-only rule.
2) Added while preparing the schedule: no new registration window; direct assignment from evening registrants.
3) Added after publication: no reopened registration and no new joining requests."""
from datetime import date

from sqlalchemy import select

from app.days import service as days
from app.days.models import ActivityDay, Shift
from tests.conftest import Client, at
from tests.helpers import board, shift
from tests.test_journal import THU, publish_evening

NOV_MON = date(2026, 11, 2)  # Monday: registration open from 28 Oct 20:00 to 31 Oct 18:00


def _shift_of(db, d, kind):
    return db.scalar(select(Shift).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                     .where(ActivityDay.local_date == d, Shift.kind == kind))


def _selectable_kinds(client, day_id):
    return {s["kind"] for s in client.get(f"/api/registrations/{day_id}").json()["shifts"]}


def test_opened_during_setup_volunteers_can_select_reinforcement(world, db, clock):
    clock.set(at("2026-10-29", "12:00"))
    day = days.create_day(db, NOV_MON, clock.now(), shift_kinds=("evening", "reinforcement"),
                          open_registration=("reinforcement",))
    db.commit()
    vol = Client("mon.trainee@x.test")
    assert {"evening", "reinforcement"} <= _selectable_kinds(vol, day.id)
    rein = _shift_of(db, NOV_MON, "reinforcement")
    r = vol.put(f"/api/registrations/{day.id}", {"choices": [{"shift_id": str(rein.id), "available": True}]})
    assert r.status_code == 200, r.text  # no trainee-only restriction


def test_not_opened_at_setup_volunteers_cannot_select_it(world, db, clock):
    clock.set(at("2026-10-29", "12:00"))
    day = days.create_day(db, NOV_MON, clock.now(), shift_kinds=("evening", "reinforcement"))
    db.commit()
    assert "reinforcement" not in _selectable_kinds(Client("mon.trainee@x.test"), day.id)


def test_added_while_preparing_assigns_from_evening_registrants_without_a_window(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    # A Thursday volunteer registers for the evening only.
    assert Client("thu.trainee@x.test").put(f"/api/registrations/{world.day(THU)}",
                                            {"choices": [{"shift_id": ev["id"], "available": True}]}).status_code == 200
    rein = shift(board(mgr, world.day(THU)), "reinforcement")
    assert rein["self_selectable"] is False  # no new window opened by the shift itself
    waiting = {w["user_id"] for w in rein["waiting"]}
    assert str(world.uid("thu_trainee")) in waiting  # direct assignment candidate from evening registrants
    assert "reinforcement" not in _selectable_kinds(Client("thu.trainee@x.test"), world.day(THU))


def test_add_with_open_before_publication_then_closed_after_publication_refuses_reopening(world, db, clock):
    clock.set(at("2026-10-29", "12:00"))
    day = days.create_day(db, NOV_MON, clock.now(), shift_kinds=("evening",))
    db.commit()
    admin = Client("admin@x.test")
    r = admin.post(f"/api/manage/days/{day.id}/shifts", {"kind": "reinforcement", "open_registration": True})
    assert r.status_code == 201, r.text
    assert "reinforcement" in _selectable_kinds(Client("mon.trainee@x.test"), day.id)
    # Publish, then the window must not be reopened and no new joining is enabled.
    from tests.helpers import assign, publish
    ev = shift(board(admin, str(day.id)), "evening")
    assign(admin, ev["slots"][0]["slot_key"], str(world.uid("mon_vol")))
    assert publish(admin, str(day.id)).status_code == 200
    rein = _shift_of(db, NOV_MON, "reinforcement")
    assert admin.post(f"/api/manage/shifts/{rein.id}/registration", {"open": True}).status_code == 409
    assert admin.post(f"/api/manage/days/{day.id}/shifts", {"kind": "reinforcement", "open_registration": True}).json()["code"] in ("shift_exists", "schedule_published")
    assert admin.post(f"/api/manage/shifts/{rein.id}/registration", {"open": False}).status_code == 200  # closing is allowed


def test_adding_a_shift_never_enables_registration_by_itself(world, db, clock):
    clock.set(at("2026-10-29", "12:00"))
    day = days.create_day(db, NOV_MON, clock.now(), shift_kinds=("evening",))
    db.commit()
    r = Client("admin@x.test").post(f"/api/manage/days/{day.id}/shifts", {"kind": "reinforcement"})
    assert r.status_code == 201
    assert _shift_of(db, NOV_MON, "reinforcement").self_selectable is False


def test_adding_reinforcement_after_publication_is_allowed_without_opening_registration(world, db, clock):
    """Adding the shift and opening registration are separate: after publication, adding Reinforcement is allowed
    (direct manager assignment), and it opens no registration or joining request."""
    clock.set(at("2026-10-29", "12:00"))
    day = days.create_day(db, NOV_MON, clock.now(), shift_kinds=("evening",))
    db.commit()
    admin = Client("admin@x.test")
    from tests.helpers import assign, publish
    ev = shift(board(admin, str(day.id)), "evening")
    assign(admin, ev["slots"][0]["slot_key"], str(world.uid("mon_vol")))
    assert publish(admin, str(day.id)).status_code == 200
    r = admin.post(f"/api/manage/days/{day.id}/shifts", {"kind": "reinforcement"})
    assert r.status_code == 201, r.text  # adding is allowed after publication
    assert _shift_of(db, NOV_MON, "reinforcement").self_selectable is False
    assert "reinforcement" not in _selectable_kinds(Client("mon.trainee@x.test"), day.id)
