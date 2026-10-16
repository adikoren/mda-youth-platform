"""Registration closing, enforced by the server (server time, Asia/Jerusalem cutoffs):
open: direct changes; after closing and before the shift starts: requests only; from the shift start:
neither. Sending a request never changes a registration or an assignment. Management stays available."""
import uuid

from sqlalchemy import func, select, text

from app.scheduling.models import Assignment
from app.core.timeutil import local_dt
from datetime import date, time
from tests.conftest import Client
from tests.helpers import board, shift

THU = "2026-10-15"  # opens 10 Oct 20:00, closes 13 Oct 18:00, evening starts 15 Oct 15:00


def local(d: str, hh: int, mm: int, ss: int = 0):
    return local_dt(date.fromisoformat(d), time(hh, mm, ss))


def evening(world):
    return shift(board(Client("thu.mgr@x.test"), world.day(THU)), "evening")["id"]


def answer(client, world, sid, available):
    return client.put(f"/api/registrations/{world.day(THU)}", {"choices": [{"shift_id": sid, "available": available}]})


def request(client, world, sid, text_):
    return client.post(f"/api/registrations/{world.day(THU)}/requests",
                       {"id": str(uuid.uuid4()), "message": text_, "shift_id": sid})


def state_of(world, db):
    """Registration choices and assignments: what a request must never touch."""
    choices = db.execute(text("select shift_id::text, available from registration_choices order by 1, 2")).all()
    return choices, db.scalar(select(func.count()).select_from(Assignment))


def test_open_registration_allows_direct_changes_and_updates(world, clock):
    clock.set(local("2026-10-12", 21, 0, 0))
    vol, sid = Client("thu.youth@x.test"), evening(world)
    assert answer(vol, world, sid, True).status_code == 200
    assert answer(vol, world, sid, False).status_code == 200  # an update while open
    assert answer(vol, world, sid, True).status_code == 200


def test_closing_boundary_at_18_00(world, clock):
    vol, sid = Client("thu.youth@x.test"), evening(world)
    clock.set(local("2026-10-13", 17, 59, 59))
    assert answer(vol, world, sid, True).status_code == 200  # still open
    clock.set(local("2026-10-13", 18, 0, 0))  # closes exactly now
    r = answer(vol, world, sid, False)
    assert r.status_code == 409 and r.json()["code"] == "registration_closed"
    assert r.json()["message"] == "ההרשמה נסגרה. לשינוי מאוחר יש לשלוח בקשה לאחראי"
    clock.set(local("2026-10-13", 18, 0, 1))
    assert answer(vol, world, sid, False).status_code == 409


def test_between_closing_and_the_shift_start_only_requests_are_possible(world, db, clock):
    vol, sid = Client("thu.youth@x.test"), evening(world)
    clock.set(local("2026-10-14", 12, 0, 0))
    before = state_of(world, db)
    assert answer(vol, world, sid, False).status_code == 409  # no direct change
    r = request(vol, world, sid, "בקשה בין הסגירה לתחילת המשמרת")
    assert r.status_code == 201
    assert state_of(world, db) == before  # the request changed no registration and no assignment


def test_at_the_shift_start_both_direct_changes_and_requests_are_refused(world, db, clock):
    vol, sid = Client("thu.youth@x.test"), evening(world)
    clock.set(local("2026-10-15", 14, 59, 59))
    assert request(vol, world, sid, "בקשה לפני התחילה").status_code == 201
    clock.set(local("2026-10-15", 15, 0, 0))
    r = request(vol, world, sid, "בקשה בתחילת המשמרת")
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
    r = answer(vol, world, sid, False)
    assert r.status_code == 409 and r.json()["code"] == "shift_started"


def test_a_form_open_when_registration_closes_is_refused_and_the_stored_answer_stands(world, clock):
    vol, sid = Client("thu.youth@x.test"), evening(world)
    clock.set(local("2026-10-13", 17, 59, 59))
    assert answer(vol, world, sid, True).status_code == 200
    clock.set(local("2026-10-13", 18, 0, 0))  # the volunteer's screen still shows open; the server says closed
    assert answer(vol, world, sid, False).json()["code"] == "registration_closed"
    shifts = vol.get(f"/api/registrations/{world.day(THU)}").json()
    assert shifts["window"] == "closed_late_allowed"
    assert next(s for s in shifts["shifts"] if s["shift_id"] == sid)["available"] is True


def test_day_manager_and_admin_keep_management_after_closing(world, clock):
    sid = evening(world)
    clock.set(local("2026-10-14", 12, 0, 0))
    assert answer(Client("thu.mgr@x.test"), world, sid, True).status_code == 200  # the relevant Day Manager
    assert answer(Client("admin@x.test"), world, sid, True).status_code == 200  # Admin
    # Another volunteer with no access to the day is not a manager.
    assert answer(Client("mon.vol@x.test"), world, sid, True).status_code == 404
