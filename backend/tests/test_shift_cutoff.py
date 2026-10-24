"""Per-shift cutoff: registration and late requests close at the shift's own start instant
(Asia/Jerusalem wall clock, stored as UTC). Boundaries tested: immediately before, exactly at, and
after the start; also the end of the shift, a multi-shift day, the DST change and Admin's override."""
from datetime import date, datetime, time, timezone

from sqlalchemy import text

from app.core.timeutil import local_dt
from tests.conftest import Client
from tests.helpers import board, shift

THU, SAT = "2026-10-15", "2026-10-17"  # Thursday evening starts 15:00 local; Saturday morning starts 07:00


def local(day: str, hh: int, mm: int, ss: int = 0) -> datetime:
    return local_dt(date.fromisoformat(day), time(hh, mm, ss))


def evening_id(world):
    return shift(board(Client("thu.mgr@x.test"), world.day(THU)), "evening")["id"]


def answer(client, world, shift_id, available=True):
    return client.put(f"/api/registrations/{world.day(THU)}",
                      {"choices": [{"shift_id": shift_id, "available": available}]})


def request(client, world, shift_id, text_: str):
    import uuid
    return client.post(f"/api/registrations/{world.day(THU)}/requests",
                       {"id": str(uuid.uuid4()), "message": text_, "shift_id": shift_id})


# ---------- registration answers: before, at, after the start ----------

def test_registration_answer_boundaries_around_closing_and_the_shift_start(world, clock):
    vol = Client("thu.youth@x.test")
    sid = evening_id(world)
    clock.set(local("2026-10-13", 17, 59, 59))
    assert answer(vol, world, sid, True).status_code == 200  # one second before closing: open, direct change allowed
    clock.set(local("2026-10-13", 18, 0, 0))  # exactly at closing: closed for volunteers
    r = answer(vol, world, sid, False)
    assert r.status_code == 409 and r.json()["code"] == "registration_closed"
    clock.set(local(THU, 15, 0, 0))  # exactly at the shift start: refused with the shift's own message
    r = answer(vol, world, sid, False)
    assert r.status_code == 409 and r.json()["code"] == "shift_started" and r.json()["message"] == "המשמרת כבר התחילה"
    clock.set(local(THU, 15, 0, 1))
    assert answer(vol, world, sid, False).status_code == 409
    # Every refused change left the earlier answer in place.
    shifts = vol.get(f"/api/registrations/{world.day(THU)}").json()["shifts"]
    assert next(s for s in shifts if s["shift_id"] == sid)["available"] is True


# ---------- late requests: before, at, after the start ----------

def test_late_request_boundaries_around_the_shift_start(world, db, clock):
    vol = Client("thu.youth@x.test")
    sid = evening_id(world)
    clock.set(local(THU, 14, 59, 59))
    assert request(vol, world, sid, "בקשה לפני תחילת המשמרת").status_code == 201
    clock.set(local(THU, 15, 0, 0))
    r = request(vol, world, sid, "בקשה בדיוק בתחילת המשמרת")
    assert r.status_code == 409 and r.json()["code"] == "shift_started" and r.json()["message"] == "המשמרת כבר התחילה"
    clock.set(local(THU, 15, 0, 1))
    assert request(vol, world, sid, "בקשה אחרי תחילת המשמרת").status_code == 409
    # Only the request before the start exists.
    assert db.scalar(text("select count(*) from registration_requests")) == 1


def test_a_form_open_when_the_shift_starts_is_refused_at_submit(world, clock):
    vol = Client("thu.youth@x.test")
    sid = evening_id(world)
    clock.set(local(THU, 14, 59, 59))  # the volunteer opens the form here
    clock.set(local(THU, 15, 0, 0))    # ...and the shift starts while it is open
    r = request(vol, world, sid, "נשלח אחרי שהמשמרת התחילה")
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
    # The screen state the server returns is the refreshed one: shift closed, no request stored.
    shifts = vol.get(f"/api/registrations/{world.day(THU)}").json()
    assert next(s for s in shifts["shifts"] if s["shift_id"] == sid)["state"] == "started"
    assert shifts["requests"] == []


# ---------- the end of the shift ----------

def test_after_the_shift_ends_both_actions_are_refused_with_the_ended_message(world, clock):
    vol = Client("thu.youth@x.test")
    sid = evening_id(world)
    clock.set(local(THU, 22, 59, 59))  # started at 15:00 and not yet ended: still refused (requests are pre-start only)
    r = request(vol, world, sid, "בקשה בזמן המשמרת")
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
    clock.set(local(THU, 23, 0, 0))  # the evening ends at 23:00
    r = request(vol, world, sid, "בקשה אחרי סיום המשמרת")
    assert r.status_code == 409 and r.json()["code"] == "shift_ended" and r.json()["message"] == "המשמרת הסתיימה"
    assert answer(vol, world, sid, False).json()["code"] == "shift_ended"
    state = vol.get(f"/api/registrations/{world.day(THU)}").json()["shifts"]
    assert next(s for s in state if s["shift_id"] == sid)["state"] == "ended"


# ---------- per shift, not per day ----------

def test_one_started_shift_does_not_close_another_on_the_same_day(world, clock):
    vol = Client("sat.mgr@x.test")  # Saturday: every volunteer may register; the manager is also a volunteer here
    b = board(Client("sat.mgr@x.test"), world.day(SAT))
    morning, evening = shift(b, "morning")["id"], shift(b, "evening")["id"]
    clock.set(local(SAT, 10, 0, 0))  # morning started at 07:00; evening starts at 15:00
    r = vol.put(f"/api/registrations/{world.day(SAT)}", {"choices": [{"shift_id": morning, "available": True}]})
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
    r = vol.put(f"/api/registrations/{world.day(SAT)}", {"choices": [{"shift_id": evening, "available": True}]})
    assert r.status_code == 409 and r.json()["code"] == "registration_closed"  # closed window: request only
    r = vol.post(f"/api/registrations/{world.day(SAT)}/requests",
                 {"id": "00000000-0000-4000-8000-000000000001", "message": "בקשה למשמרת הערב", "shift_id": morning})
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
    r = vol.post(f"/api/registrations/{world.day(SAT)}/requests",
                 {"id": "00000000-0000-4000-8000-000000000002", "message": "בקשה למשמרת הערב", "shift_id": evening})
    assert r.status_code == 201


# ---------- Asia/Jerusalem and daylight saving ----------

def test_cutoff_uses_the_real_start_instant_across_the_dst_change(world, db, clock):
    # Israel leaves summer time on 25 Oct 2026. Saturday 24 Oct 15:00 local is 12:00 UTC;
    # Monday 26 Oct 15:00 local is 13:00 UTC. The stored instants must reflect that.
    starts = dict(db.execute(text(
        "select d.local_date::text, s.starts_at from shifts s join activity_days d on d.id = s.activity_day_id "
        "where s.kind = 'evening' and d.local_date in ('2026-10-24', '2026-10-26')")).all())
    assert starts["2026-10-24"] == datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    assert starts["2026-10-26"] == datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc)
    vol = Client("mon.vol@x.test")  # volunteer with Monday as home day
    clock.set(datetime(2026, 10, 26, 12, 59, 59, tzinfo=timezone.utc))
    shifts = vol.get(f"/api/registrations/{world.day('2026-10-26')}").json()["shifts"]
    assert next(s for s in shifts if s["kind"] == "evening")["state"] == "before"
    clock.set(datetime(2026, 10, 26, 13, 0, 0, tzinfo=timezone.utc))
    shifts = vol.get(f"/api/registrations/{world.day('2026-10-26')}").json()["shifts"]
    assert next(s for s in shifts if s["kind"] == "evening")["state"] == "started"


# ---------- Admin keeps management permissions ----------

def test_admin_gets_the_volunteer_cutoff_for_their_own_registration(world, clock):
    admin = Client("admin@x.test")
    sid = evening_id(world)
    clock.set(local(THU, 15, 0, 1))
    r = answer(admin, world, sid, True)  # Admin is not exempt for their own registration
    assert r.status_code == 409 and r.json()["code"] == "shift_started"
