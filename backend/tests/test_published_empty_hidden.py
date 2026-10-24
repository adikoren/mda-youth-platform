"""Published schedules show only populated ambulances, and a shift with no populated ambulance is hidden
(decision 2026-10-16). The management board keeps empty shifts and ambulances, and no record is deleted."""
from sqlalchemy import func, select

from app.scheduling.models import AmbulanceSlot
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, shift
from tests.test_journal import THU

MON_NEW = "2026-10-19"


def test_partially_assigned_schedule_shows_only_populated_ambulances_and_hides_empty_shifts(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    ev = shift(b, "evening")
    morning = shift(b, "morning")
    # Evening: ambulance 1 gets one volunteer, ambulance 2 stays empty. Morning: nobody at all.
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_youth"))
    assert publish(mgr, world.day(THU)).status_code == 200

    view = Client("thu.youth@x.test").get(f"/api/schedule/{world.day(THU)}").json()
    kinds = [s["kind"] for s in view["shifts"]]
    assert kinds == ["evening"]  # the empty morning shift is hidden entirely
    ambulances = view["shifts"][0]["slots"]
    assert [a["ordinal"] for a in ambulances] == [1]  # the empty ambulance 2 is not shown
    assert all(a["crew"] for a in ambulances)

    # Management still sees every shift and ambulance, empty ones included, and nothing was deleted.
    board_view = board(mgr, world.day(THU))
    assert {s["kind"] for s in board_view["shifts"]} >= {"morning", "evening"}
    assert db.scalar(select(func.count()).select_from(AmbulanceSlot).where(
        AmbulanceSlot.shift_id == morning["id"])) >= 1


def test_schedule_history_follows_the_same_rule(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_youth"))
    publish(mgr, world.day(THU))
    clock.set(at("2026-10-20", "12:00"))  # the date is historical now
    view = Client("thu.youth@x.test").get(f"/api/schedule/{world.day(THU)}").json()
    assert [s["kind"] for s in view["shifts"]] == ["evening"]
