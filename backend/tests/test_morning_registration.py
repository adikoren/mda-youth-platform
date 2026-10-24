"""Sunday–Friday morning shifts are hidden from volunteer registration until a manager opens them;
Saturday keeps morning and evening (decision 2026-10-12)."""
from sqlalchemy import select

from app.audit.models import AuditEvent
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, register, shift

THU, FRI, SAT = "2026-10-15", "2026-10-16", "2026-10-17"


def kinds(client, day_id):
    r = client.get(f"/api/registrations/{day_id}")
    assert r.status_code == 200, r.text
    return [s["kind"] for s in r.json()["shifts"]]


def test_weekday_and_friday_mornings_are_hidden_saturday_keeps_both(world, clock):
    clock.set(at("2026-10-12", "21:00"))  # Thursday, Friday and Saturday registration are open
    vol = Client("thu.youth@x.test")
    assert kinds(vol, world.day(THU)) == ["evening"]
    assert kinds(Client("mon.vol@x.test"), world.day(SAT)) == ["morning", "evening"]
    fri = Client("admin@x.test")  # Friday has no day manager in the fixture: admin manages it
    b = board(fri, world.day(FRI))
    morning = shift(b, "morning")
    # The morning exists for scheduling, but creating it did not open registration.
    assert morning["self_selectable"] is False and morning["registration_toggle"] is True
    assert shift(board(Client("sat.mgr@x.test"), world.day(SAT)), "morning")["registration_toggle"] is False


def test_manager_opens_morning_explicitly_and_closing_preserves_answers(world, clock, db):
    clock.set(at("2026-10-12", "21:00"))
    mgr, vol = Client("thu.mgr@x.test"), Client("thu.youth@x.test")
    morning = shift(board(mgr, world.day(THU)), "morning")
    # Not visible, and a crafted request cannot register for it either.
    r = vol.put(f"/api/registrations/{world.day(THU)}", {"choices": [{"shift_id": morning["id"], "available": True}]})
    assert r.status_code == 400 and r.json()["code"] == "shift_not_selectable"
    # Only managers of that date (or admin) may open it.
    assert vol.post(f"/api/manage/shifts/{morning['id']}/registration", {"open": True}).status_code in (403, 404)
    assert Client("sat.mgr@x.test").post(f"/api/manage/shifts/{morning['id']}/registration", {"open": True}).status_code in (403, 404)
    r = mgr.post(f"/api/manage/shifts/{morning['id']}/registration", {"open": True})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert kinds(vol, world.day(THU)) == ["morning", "evening"]
    assert register(vol, world.day(THU), morning=True, evening=True).status_code == 200
    assign(mgr, morning["slots"][0]["slot_key"], world.uid("thu_youth"))
    assert publish(mgr, world.day(THU)).status_code == 200

    # Closing hides it again, but the answer, the assignment and history stay.
    assert mgr.post(f"/api/manage/shifts/{morning['id']}/registration", {"open": False}).status_code == 200
    assert kinds(vol, world.day(THU)) == ["evening"]
    b = board(mgr, world.day(THU))
    assert [p["user_id"] for p in shift(b, "morning")["slots"][0]["crew"]] == [world.uid("thu_youth")]
    assert any(h["shift"] == "morning" for h in vol.get("/api/me/history").json())
    # Saving the visible shifts later does not touch the hidden morning answer.
    r = register(vol, world.day(THU), evening=False)  # after publication: refused
    assert r.status_code == 409 and r.json()["code"] == "schedule_published"
    board_after = board(mgr, world.day(THU))
    assert shift(board_after, "morning")["slots"][0]["crew"][0]["source"] in ("self", "both")
    actions = [a.action for a in db.scalars(select(AuditEvent).where(AuditEvent.action.like("morning_registration_%")))]
    assert sorted(actions) == ["morning_registration_close", "morning_registration_open"]


def test_saturday_morning_cannot_be_closed_and_evening_is_not_toggleable(world, clock):
    mgr = Client("sat.mgr@x.test")
    b = board(mgr, world.day(SAT))
    r = mgr.post(f"/api/manage/shifts/{shift(b, 'morning')['id']}/registration", {"open": False})
    assert r.status_code == 409 and r.json()["code"] == "saturday_fixed"
    r = Client("thu.mgr@x.test").post(f"/api/manage/shifts/{shift(board(Client('thu.mgr@x.test'), world.day(THU)), 'evening')['id']}/registration",
                                      {"open": False})
    assert r.status_code == 400
