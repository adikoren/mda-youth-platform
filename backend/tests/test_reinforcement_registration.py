"""Opening registration for reinforcement (decision of 2026-10-17): the same switch as Morning. A Day Manager opens
or closes it for their own weekday; volunteers see it only while open; closing keeps existing answers; assignments
are untouched; the day lock and the day-ended rule apply."""
from sqlalchemy import select

from app.days.models import Shift
from tests.conftest import Client, at
from tests.helpers import board, shift
from tests.test_journal import THU

THU_MGR = "thu.mgr@x.test"


def reinf_id(world):
    return shift(board(Client(THU_MGR), world.day(THU)), "reinforcement")["id"]


def toggle(email, shift_id, open_):
    return Client(email).post(f"/api/manage/shifts/{shift_id}/registration", {"open": open_})


def test_day_manager_opens_and_closes_reinforcement_registration(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    sid = reinf_id(world)
    assert toggle(THU_MGR, sid, True).json()["changed"] is True
    assert toggle(THU_MGR, sid, True).json()["changed"] is False  # idempotent
    assert toggle(THU_MGR, sid, False).json()["changed"] is True


def test_open_reinforcement_appears_to_volunteers_and_can_be_answered(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    sid = reinf_id(world)
    toggle(THU_MGR, sid, True)
    vol = Client("thu.trainee@x.test")
    reg = vol.get(f"/api/registrations/{world.day(THU)}").json()
    assert sid in {s["shift_id"] for s in reg["shifts"]}
    assert vol.put(f"/api/registrations/{world.day(THU)}", {"choices": [{"shift_id": sid, "available": True}]}).status_code == 200


def test_closing_hides_it_and_keeps_existing_answers(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    sid = reinf_id(world)
    toggle(THU_MGR, sid, True)
    vol = Client("thu.trainee@x.test")
    vol.put(f"/api/registrations/{world.day(THU)}", {"choices": [{"shift_id": sid, "available": True}]})
    toggle(THU_MGR, sid, False)
    assert sid not in {s["shift_id"] for s in vol.get(f"/api/registrations/{world.day(THU)}").json()["shifts"]}
    kept = db.scalar(select(Shift.self_selectable).where(Shift.id == sid))
    assert kept is False


def test_only_the_day_manager_or_admin_toggles(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    sid = reinf_id(world)
    assert toggle("thu.youth@x.test", sid, True).status_code in (403, 404)
    assert toggle("sat.mgr@x.test", sid, True).status_code in (403, 404)
    assert toggle("admin@x.test", sid, True).status_code == 200


def test_toggling_never_assigns_anyone(world, db, clock):
    from app.scheduling.models import Assignment
    clock.set(at("2026-10-12", "21:00"))
    before = db.query(Assignment).count()
    toggle(THU_MGR, reinf_id(world), True)
    assert db.query(Assignment).count() == before


def test_evening_is_still_not_toggleable(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    ev = shift(board(Client(THU_MGR), world.day(THU)), "evening")["id"]
    assert toggle(THU_MGR, ev, True).json()["code"] == "not_toggleable"
