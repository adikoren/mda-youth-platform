"""Volunteer self-registration rules, enforced on the server for every shift type (morning, evening, reinforcement).

Direct registration: only while the window is open, the schedule is unpublished, and the shift has not started.
Join requests: only after closing, before publication, before the shift starts.
Regression: the window of a day can be open while the schedule is published or the shift has started. The
tests below force that state (closing time moved past the start) to prove the server still refuses.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy import select, update

from app.core.timeutil import local_dt
from app.days.models import ActivityDay, Shift
from tests.conftest import Client, at
from tests.helpers import board, publish, register, shift
from tests.test_reinforcement_registration import toggle

THU = "2026-10-15"  # evening 15:00-23:00, morning 07:00-15:00 (opened by the Day Manager), reinforcement (opened)
THU_MGR = "thu.mgr@x.test"
VOL = "thu.youth@x.test"
EARLY = "2026-10-11", "12:00"  # before publication, window open, nothing started


def local(d: str, hh: int, mm: int, ss: int = 0) -> datetime:
    return local_dt(date.fromisoformat(d), time(hh, mm, ss))


def open_all_shift_types(world, db, clock):
    """Morning and reinforcement are opened by the Day Manager; evening is open by default. Also marks the window
    as open past the shift start (the state the report described): the server must still refuse."""
    mgr = Client(THU_MGR)
    b = board(mgr, world.day(THU))
    for kind in ("morning", "reinforcement"):
        assert toggle(THU_MGR, shift(b, kind)["id"], True).status_code == 200
    db.execute(update(ActivityDay).where(ActivityDay.id == world.day(THU))
               .values(registration_closes_at=local(THU, 23, 0)))
    db.commit()
    clock.set(local(*EARLY[:1], *map(int, EARLY[1].split(":"))))


def shift_ids(db, world) -> dict[str, tuple[str, datetime]]:
    rows = db.execute(select(Shift.id, Shift.kind, Shift.starts_at, Shift.ends_at)
                      .where(Shift.activity_day_id == world.day(THU), Shift.self_selectable.is_(True))).all()
    return {kind: (str(sid), starts, ends) for sid, kind, starts, ends in rows}


def put_one(client, world, sid, available=True):
    return client.put(f"/api/registrations/{world.day(THU)}",
                      {"choices": [{"shift_id": sid, "available": available}]})


def request_one(client, world, sid, rid, text="בקשה להצטרפות"):
    return client.post(f"/api/registrations/{world.day(THU)}/requests",
                       {"id": rid, "message": text, "shift_id": sid})


def test_before_publication_and_before_start_direct_registration_works_for_every_shift_type(world, db, clock):
    open_all_shift_types(world, db, clock)
    vol = Client(VOL)
    for kind, (sid, _, _) in shift_ids(db, world).items():
        r = put_one(vol, world, sid)
        assert r.status_code == 200, (kind, r.text)


def test_direct_requests_are_rejected_after_publication_even_while_the_window_is_open(world, db, clock):
    open_all_shift_types(world, db, clock)
    vol, mgr = Client(VOL), Client(THU_MGR)
    evening = shift_ids(db, world)["evening"][0]
    assert put_one(vol, world, evening).status_code == 200  # valid before publication
    assert publish(mgr, world.day(THU)).status_code == 200

    for kind, (sid, _, _) in shift_ids(db, world).items():
        r = put_one(vol, world, sid, available=False)
        assert r.status_code == 409 and r.json()["code"] == "schedule_published", (kind, r.text)
        r = request_one(vol, world, sid, f"00000000-0000-4000-8000-0000000000{kind[:2].encode().hex()[:2]}")
        assert r.status_code == 409 and r.json()["code"] == "schedule_published", (kind, r.text)
    state = vol.get(f"/api/registrations/{world.day(THU)}").json()
    assert state["published"] is True
    assert all(s["direct"] is False and s["request"] is False for s in state["shifts"])
    assert next(s for s in state["shifts"] if s["kind"] == "evening")["available"] is True  # unchanged


def test_exactly_at_shift_start_registration_and_requests_are_refused_for_every_shift_type(world, db, clock):
    open_all_shift_types(world, db, clock)
    vol = Client(VOL)
    for kind, (sid, starts, _) in shift_ids(db, world).items():
        clock.set(starts - timedelta(seconds=1))
        assert put_one(vol, world, sid).status_code == 200, kind  # one second before: still allowed
        clock.set(starts)
        r = put_one(vol, world, sid, available=False)
        assert r.status_code == 409 and r.json()["code"] == "shift_started", (kind, r.text)
        r = request_one(vol, world, sid, f"00000000-0000-4000-8000-0000000001{kind[:2].encode().hex()[:2]}")
        assert r.status_code == 409 and r.json()["code"] == "shift_started", (kind, r.text)


def test_volunteer_cancellation_is_refused_at_and_after_shift_start(world, db, clock):
    open_all_shift_types(world, db, clock)
    vol = Client(VOL)
    evening, starts = shift_ids(db, world)["evening"][0], shift_ids(db, world)["evening"][1]
    assert put_one(vol, world, evening).status_code == 200
    clock.set(starts)
    r = vol.post(f"/api/shifts/{evening}/cancel", {})
    assert r.status_code == 409 and r.json()["code"] == "shift_started", r.text


def test_after_the_shift_ends_everything_is_refused(world, db, clock):
    open_all_shift_types(world, db, clock)
    vol = Client(VOL)
    for kind, (sid, _, ends) in shift_ids(db, world).items():
        clock.set(ends)
        r = put_one(vol, world, sid)
        assert r.status_code == 409 and r.json()["code"] == "shift_ended", (kind, r.text)
        r = request_one(vol, world, sid, f"00000000-0000-4000-8000-0000000002{kind[:2].encode().hex()[:2]}")
        assert r.status_code == 409 and r.json()["code"] == "shift_ended", (kind, r.text)


def test_stale_form_submitted_after_publication_is_refused(world, db, clock):
    """A form opened before publication still holds the old choice. Submitting it must not change anything."""
    open_all_shift_types(world, db, clock)
    vol, mgr = Client(VOL), Client(THU_MGR)
    evening = shift_ids(db, world)["evening"][0]
    stale_form = {"choices": [{"shift_id": evening, "available": True}], "expected_revision": 0}  # opened early
    assert publish(mgr, world.day(THU)).status_code == 200
    r = vol.put(f"/api/registrations/{world.day(THU)}", stale_form)
    assert r.status_code == 409 and r.json()["code"] == "schedule_published", r.text
    assert vol.get(f"/api/registrations/{world.day(THU)}").json()["revision"] == 0


def test_stale_request_form_submitted_after_the_shift_started_is_refused(world, db, clock):
    open_all_shift_types(world, db, clock)
    vol = Client(VOL)
    evening, starts = shift_ids(db, world)["evening"][0], shift_ids(db, world)["evening"][1]
    clock.set(starts - timedelta(seconds=1))  # the request form is still open here
    clock.set(starts)  # submitted at the start instant
    r = request_one(vol, world, evening, "00000000-0000-4000-8000-000000000301")
    assert r.status_code == 409 and r.json()["code"] == "shift_started", r.text


def test_admin_is_held_to_the_same_rules_for_their_own_registration(world, db, clock):
    open_all_shift_types(world, db, clock)
    admin, mgr = Client("admin@x.test"), Client(THU_MGR)
    evening, starts = shift_ids(db, world)["evening"][0], shift_ids(db, world)["evening"][1]
    assert put_one(admin, world, evening).status_code == 200  # before publication and start: allowed
    clock.set(starts)
    r = put_one(admin, world, evening, available=False)
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
    clock.set(local(THU, 9, 0))
    assert publish(mgr, world.day(THU)).status_code == 200
    r = put_one(admin, world, evening, available=False)
    assert r.status_code == 409 and r.json()["code"] == "schedule_published"


def test_listing_all_dates_does_not_enable_self_registration(world, db, clock):
    """Admin sees every date in the registration list; seeing a date must not grant the right to answer it."""
    open_all_shift_types(world, db, clock)
    published_ok = Client("admin@x.test").get(f"/api/registrations/{world.day(THU)}")
    assert published_ok.status_code == 200
    assert publish(Client(THU_MGR), world.day(THU)).status_code == 200
    r = Client("admin@x.test").get(f"/api/registrations/{world.day(THU)}")
    assert r.status_code == 200 and r.json()["published"] is True
    assert all(s["direct"] is False for s in r.json()["shifts"])


def test_schedule_visible_follows_the_schedule_viewing_rule(world, db, clock):
    """The registration page links to the published schedule only where the schedule page would open it."""
    open_all_shift_types(world, db, clock)
    assert publish(Client(THU_MGR), world.day(THU)).status_code == 200
    thursday_volunteer = Client(VOL)  # home day Thursday: may view the Thursday schedule
    monday_volunteer = Client("mon.vol@x.test")  # not assigned, other day: may not
    assert thursday_volunteer.get(f"/api/registrations/{world.day(THU)}").json()["schedule_visible"] is True
    assert monday_volunteer.get(f"/api/registrations/{world.day(THU)}").status_code == 404 or \
        monday_volunteer.get(f"/api/registrations/{world.day(THU)}").json()["schedule_visible"] is False


def test_late_request_is_rejected_after_publication_even_from_a_stale_page(world, db, clock):
    """Closed before publication: the late-request form was open on the page; publication then happens."""
    clock.set(at("2026-10-14", "12:00"))  # closed; late requests are allowed until the shift starts
    vol = Client(VOL)
    evening = shift_ids_closed(db, world)["evening"][0]
    assert request_one(vol, world, evening, "00000000-0000-4000-8000-000000000401").status_code == 201
    mgr = Client(THU_MGR)
    board(mgr, world.day(THU))
    rid = vol.get(f"/api/registrations/{world.day(THU)}").json()["requests"][-1]["id"]
    assert mgr.post(f"/api/manage/requests/{rid}/decision", {"decision": "rejected"}).status_code == 200  # decide first
    assert publish(mgr, world.day(THU)).status_code == 200
    r = request_one(vol, world, evening, "00000000-0000-4000-8000-000000000402", "בקשה נוספת")
    assert r.status_code == 409 and r.json()["code"] == "schedule_published", r.text


def shift_ids_closed(db, world):
    rows = db.execute(select(Shift.id, Shift.kind, Shift.starts_at, Shift.ends_at)
                      .where(Shift.activity_day_id == world.day(THU), Shift.self_selectable.is_(True))).all()
    return {kind: (str(sid), starts, ends) for sid, kind, starts, ends in rows}
