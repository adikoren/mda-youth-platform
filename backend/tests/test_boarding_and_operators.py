"""Crew display order, publication with empty trainee positions, the boarding order (סדר עלייה), operating
volunteers on additional ambulances, and boarded trainees as scheduled participants (decision 2026-10-16)."""
from sqlalchemy import select

from app.notifications.models import OutboxMessage
from app.days.models import Shift, ActivityDay
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, shift
from tests.test_journal import THU

PUB = at("2026-10-12", "21:00")


def ev_id(world):
    return shift(board(Client("thu.mgr@x.test"), world.day(THU)), "evening")


def put_boarding(world, entries):
    return Client("thu.mgr@x.test").put(f"/api/manage/days/{world.day(THU)}/boarding", {"entries": entries})


def view(client_email, world):
    r = Client(client_email).get(f"/api/schedule/{world.day(THU)}")
    assert r.status_code == 200, r.text
    return r.json()


def test_publication_with_empty_trainee_positions_keeps_the_populated_ambulance(world, clock):
    clock.set(PUB)
    mgr = Client("thu.mgr@x.test")
    ev = ev_id(world)
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_mentor"))  # no trainee placed anywhere
    assert publish(mgr, world.day(THU)).status_code == 200
    slot = view("thu.youth@x.test", world)["shifts"][0]["slots"][0]
    assert [p["level"] for p in slot["crew"]] == ["mentor"]
    assert slot["empty_trainee_positions"] == slot["capacity"] - 1 > 0


def test_crew_is_shown_mentor_then_youth_then_trainee(world, clock):
    clock.set(PUB)
    mgr = Client("thu.mgr@x.test")
    ev = ev_id(world)
    slot_key = ev["slots"][0]["slot_key"]
    for key in ("thu_trainee", "thu_youth", "thu_mentor"):  # assigned in the wrong order on purpose
        assign(mgr, slot_key, world.uid(key))
    publish(mgr, world.day(THU))
    crew = view("thu.youth@x.test", world)["shifts"][0]["slots"][0]["crew"]
    assert [p["level"] for p in crew] == ["mentor", "youth", "trainee"]


def test_boarding_list_persists_reorders_and_publishes(world, clock):
    clock.set(PUB)
    mgr = Client("thu.mgr@x.test")
    ev = ev_id(world)
    a, b = str(world.uid("thu_trainee")), str(world.uid("thu_trainee2"))
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_mentor"))
    assert put_boarding(world, [{"user_id": a, "shift_id": ev["id"]}, {"user_id": b, "shift_id": ev["id"]}]).status_code == 200
    order = [e["user_id"] for e in board(mgr, world.day(THU))["boarding"]]
    assert order == [a, b]
    # Reorder: reversed order is saved and shown.
    assert put_boarding(world, [{"user_id": b, "shift_id": ev["id"]}, {"user_id": a, "shift_id": ev["id"]}]).status_code == 200
    assert [e["user_id"] for e in board(mgr, world.day(THU))["boarding"]] == [b, a]
    assert publish(mgr, world.day(THU)).status_code == 200
    assert [e["user_id"] for e in view("thu.youth@x.test", world)["boarding"]] == [b, a]


def test_boarding_is_scheduled_participation_not_waiting_and_not_unassigned(world, db, clock):
    clock.set(PUB)
    mgr = Client("thu.mgr@x.test")
    ev = ev_id(world)
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_mentor"))
    mon = str(world.uid("mon_trainee"))  # home day Monday, boarded on Thursday
    assert put_boarding(world, [{"user_id": mon, "shift_id": ev["id"]}]).status_code == 200
    # Not counted as waiting on the board (scheduled, not unassigned).
    waiting = [w["user_id"] for w in board(mgr, world.day(THU))["shifts"][0]["waiting"]]
    assert mon not in waiting
    publish(mgr, world.day(THU))
    # Visible to the boarded trainee even though they are a non-home-day guest, with no ambulance yet.
    trainee = Client("mon.trainee@x.test")
    assert trainee.get(f"/api/schedule/{world.day(THU)}").status_code == 200
    clock.set(at("2026-10-14", "12:00"))
    card = trainee.get("/api/me/next-assignment").json()["assignment"]
    assert card is not None and card["ambulance"] is None and card["kind"] == "evening"
    history = trainee.get("/api/me/history").json()
    assert any(h["date"] == "2026-10-15" and h["ambulance"] is None for h in history)
    # Notified like an assigned person (scheduled participation).
    msgs = db.scalars(select(OutboxMessage).where(OutboxMessage.recipient_user_id == world.uid("mon_trainee"),
                                                  OutboxMessage.event_type == "assigned")).all()
    assert msgs


def test_boarding_rejects_non_trainees_other_shifts_and_duplicates(world, clock):
    clock.set(PUB)
    ev = ev_id(world)
    assert put_boarding(world, [{"user_id": str(world.uid("thu_youth")), "shift_id": ev["id"]}]).json()["code"] == "boarding_not_trainee"
    morning = shift(board(Client("thu.mgr@x.test"), world.day(THU)), "morning")
    assert put_boarding(world, [{"user_id": str(world.uid("thu_trainee")), "shift_id": morning["id"]}]).json()["code"] == "boarding_shift"
    t = str(world.uid("thu_trainee"))
    assert put_boarding(world, [{"user_id": t, "shift_id": ev["id"]}, {"user_id": t, "shift_id": ev["id"]}]).json()["code"] == "boarding_duplicate"


def test_operating_volunteer_from_a_start_time_and_explicit_range(world, clock):
    clock.set(PUB)
    mgr = Client("thu.mgr@x.test")
    ev = ev_id(world)
    slot_key = ev["slots"][1]["slot_key"]
    youth = str(world.uid("thu_youth"))
    r = mgr.put(f"/api/manage/slots/{slot_key}/operator", {"user_id": youth, "start": "18:00"})
    assert r.status_code == 200, r.text
    assert mgr.put(f"/api/manage/slots/{slot_key}/operator", {"user_id": youth, "start": "18:00", "end": "20:00"}).status_code == 200
    # Start outside the shift and end before start are refused.
    assert mgr.put(f"/api/manage/slots/{slot_key}/operator", {"user_id": youth, "start": "12:00"}).status_code == 400
    assert mgr.put(f"/api/manage/slots/{slot_key}/operator", {"user_id": youth, "start": "19:00", "end": "18:00"}).status_code == 400
    # Publish with the operator on an ambulance that has no volunteer crew: the ambulance is not populated, so hidden.
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_mentor"))
    publish(mgr, world.day(THU))
    slots = view("thu.youth@x.test", world)["shifts"][0]["slots"]
    assert len(slots) == 1  # the operated but empty ambulance is hidden from the published view
    # Put a crew member on the operated ambulance: now it shows the operator and the explicit range.
    mgr2 = Client("thu.mgr@x.test")
    board(mgr2, world.day(THU))
    assign(mgr2, slot_key, world.uid("thu_trainee"))
    publish(mgr2, world.day(THU))
    op = next(s for s in view("thu.youth@x.test", world)["shifts"][0]["slots"] if s["operator"])["operator"]
    assert op["full_name"] and op["start"] == "18:00" and op["end"] == "20:00"


def test_cancelling_a_boarded_place_removes_it_from_the_published_list(world, clock):
    clock.set(PUB)
    mgr = Client("thu.mgr@x.test")
    ev = ev_id(world)
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_mentor"))
    put_boarding(world, [{"user_id": str(world.uid("mon_trainee")), "shift_id": ev["id"]}])
    publish(mgr, world.day(THU))
    assert len(view("thu.youth@x.test", world)["boarding"]) == 1
    r = Client("mon.trainee@x.test").post(f"/api/shifts/{ev['id']}/cancel", {})
    assert r.status_code == 200 and r.json()["published_cancelled"] is True, r.text
    assert view("thu.youth@x.test", world)["boarding"] == []
