from sqlalchemy import select

from app.notifications.models import OutboxMessage
from app.registration.models import AvailabilityAttention
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, register, shift, shifts_of

THU = "2026-10-15"  # opens Sat 10 Oct 20:00, closes Tue 13 Oct 18:00
SAT = "2026-10-17"  # opens Mon 12 Oct 20:00, closes Thu 15 Oct 18:00


def test_ordinary_day_offers_evening_only_with_optional_arrival(world):
    c = Client("thu.trainee@x.test")
    assert set(shifts_of(c, world.day(THU))) == {"evening"}
    r = register(c, world.day(THU), evening="16:00")
    assert r.status_code == 200 and r.json()["late"] is False
    assert r.json()["registration"]["shifts"][0]["arrival_time"] == "16:00"


def test_before_opening_is_blocked(world, clock):
    c = Client("thu.trainee@x.test")
    r = register(c, world.day("2026-10-22"), evening=True)  # opens Sat 17 Oct 20:00
    assert r.status_code == 409 and r.json()["code"] == "registration_not_open"
    clock.set(at("2026-10-17", "20:00"))
    assert register(c, world.day("2026-10-22"), evening=True).status_code == 200


def test_volunteers_and_the_day_manager_cannot_change_answers_after_closing(world, clock, db):
    clock.set(at("2026-10-13", "18:00"))  # exactly at closing: closed for everyone; requests only
    for who in ("thu.youth@x.test", "thu.mgr@x.test"):
        r = register(Client(who), world.day(THU), evening=True)
        assert r.status_code == 409 and r.json()["code"] == "registration_closed", who
    assert db.query(OutboxMessage).filter_by(event_type="late_registration").count() == 0


def test_saturday_open_to_everyone_with_independent_multi_select(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    c = Client("mon.vol@x.test")  # Monday home day, no invitation needed for Saturday
    ids = shifts_of(c, world.day(SAT))
    assert set(ids) == {"morning", "evening"}
    r = register(c, world.day(SAT), morning=True, evening=True)
    assert r.status_code == 200
    # Changing evening alone preserves the morning choice.
    r = c.put(f"/api/registrations/{world.day(SAT)}",
              {"choices": [{"shift_id": ids["evening"], "available": False}]})
    got = {s["kind"]: s["available"] for s in r.json()["registration"]["shifts"]}
    assert got == {"morning": True, "evening": False}
    # Full shifts only: no arrival time on special days.
    r = c.put(f"/api/registrations/{world.day(SAT)}",
              {"choices": [{"shift_id": ids["morning"], "available": True, "arrival_time": "08:00"}]})
    assert r.status_code == 400


def test_other_day_requires_invitation(world):
    mon = Client("mon.vol@x.test")
    assert mon.get(f"/api/registrations/{world.day(THU)}").status_code == 404
    assert all(d["date"] != THU for d in mon.get("/api/registrations").json())
    assert mon.get("/api/invitations").json() == []  # no invitation UI without an active invitation
    mgr = Client("thu.mgr@x.test")
    assert mgr.post(f"/api/manage/days/{world.day(THU)}/invitations", {"user_id": world.uid("mon_vol")}).status_code == 201
    cards = mon.get("/api/invitations").json()
    assert len(cards) == 1 and cards[0]["date"] == THU and cards[0]["kind"] == "invitation"
    assert register(mon, world.day(THU), evening=True).status_code == 200
    assert mon.get("/api/invitations").json() == []  # answered -> card disappears


def test_reinforcement_and_ordinary_morning_are_not_self_selectable(world, db):
    c = Client("thu.trainee@x.test")
    b = board(Client("thu.mgr@x.test"), world.day(THU))
    r = c.put(f"/api/registrations/{world.day(THU)}",
              {"choices": [{"shift_id": shift(b, "reinforcement")["id"], "available": True}]})
    assert r.status_code == 400


def test_withdrawal_after_publication_is_refused_and_the_assignment_stays(world, db):
    trainee = Client("thu.trainee@x.test")
    register(trainee, world.day(THU), evening=True)
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    ev = shift(b, "evening")
    assert assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee")).status_code == 201
    assert publish(mgr, world.day(THU)).status_code == 200

    r = register(trainee, world.day(THU), evening=False)  # after publication: refused, not silently applied
    assert r.status_code == 409 and r.json()["code"] == "schedule_published"
    sched = trainee.get(f"/api/schedule/{world.day(THU)}").json()
    crew = [p["user_id"] for s in sched["shifts"] for sl in s["slots"] for p in sl["crew"]]
    assert world.uid("thu_trainee") in crew  # still assigned until a manager acts
    b = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()
    # A refused change leaves no trace: no attention for the manager and no withdrawal notice.
    assert not any(a["label"] == "ביטל זמינות — נדרש טיפול" for a in b["attentions"])
    assert db.query(OutboxMessage).filter_by(event_type="availability_withdrawn").count() == 0


def test_registration_list_visible_to_youth_manager_and_scope_only(world):
    register(Client("thu.trainee@x.test"), world.day(THU), evening=True)
    assert len(Client("youthmgr@x.test").get(f"/api/manage/days/{world.day(THU)}/registrations").json()) == 1
    assert len(Client("thu.mgr@x.test").get(f"/api/manage/days/{world.day(THU)}/registrations").json()) == 1
    assert Client("sat.mgr@x.test").get(f"/api/manage/days/{world.day(THU)}/registrations").status_code == 404
    assert Client("thu.youth@x.test").get(f"/api/manage/days/{world.day(THU)}/registrations").status_code == 404
