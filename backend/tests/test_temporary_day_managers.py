"""Temporary Day Manager appointments (decision of 2026-10-17): one permanent weekday per person, time-limited
coverage of another weekday, validity checked on every request, overlap rules, authorization, eligibility,
Asia/Jerusalem times, history, and notification recipients."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.accounts.models import ManagerDayScope, TemporaryDayManagerAppointment
from app.notifications.outbox import day_manager_ids
from tests.conftest import Client, at

TUE = "2026-10-20"  # Tuesday: nobody permanently manages it in the fixture
THU = "2026-10-22"  # Thursday: thu.manager is the permanent Day Manager
TUE_WD, THU_WD = 2, 4
YM = "youthmgr@x.test"


def temp(client, user_id, wd, starts, ends):
    return client.post("/api/day-appointments/temporary", {"user_id": str(user_id), "weekday": wd,
                                                           "starts": starts, "ends": ends})


def board(email, day_date, world):
    return Client(email).get(f"/api/manage/days/{world.day(day_date)}/board")


@pytest.fixture
def base_clock(clock):
    clock.set(at("2026-10-12", "21:00"))
    return clock


def test_a_second_permanent_weekday_is_rejected(world, base_clock):
    r = Client(YM).put("/api/day-managers/1", {"user_ids": [str(world.uid("thu_mgr"))]})
    assert r.status_code == 409 and r.json()["code"] == "already_permanent_manager"


def test_the_database_refuses_a_second_permanent_scope_for_one_person(world, db):
    db.add(ManagerDayScope(user_id=world.uid("thu_mgr"), weekday=1, valid_from=datetime.now(timezone.utc)))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_coverage_of_another_weekday_applies_only_inside_the_interval(world, base_clock, clock):
    r = temp(Client(YM), world.uid("thu_mgr"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00")
    assert r.status_code == 201, r.text
    clock.set(at(TUE, "07:59"))
    assert board("thu.mgr@x.test", TUE, world).status_code == 404  # before start: no permission
    clock.set(at(TUE, "08:00"))
    assert board("thu.mgr@x.test", TUE, world).status_code == 200  # start is inclusive
    clock.set(at(TUE, "11:59"))
    assert board("thu.mgr@x.test", TUE, world).status_code == 200
    clock.set(at(TUE, "12:00"))
    assert board("thu.mgr@x.test", TUE, world).status_code == 404  # end is exclusive
    # The permanent day is untouched throughout.
    assert board("thu.mgr@x.test", THU, world).status_code == 200
    me = Client("thu.mgr@x.test").get("/api/auth/me").json()
    assert me["managed_weekdays"] == [4] and me["temporary_days"] == []


def test_revocation_removes_access_immediately(world, base_clock, clock):
    appt = temp(Client(YM), world.uid("thu_mgr"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00").json()
    clock.set(at(TUE, "09:00"))
    assert board("thu.mgr@x.test", TUE, world).status_code == 200
    assert Client(YM).post(f"/api/day-appointments/temporary/{appt['id']}/revoke", {"confirm": True}).status_code == 200
    assert board("thu.mgr@x.test", TUE, world).status_code == 404


def test_expiry_keeps_the_original_roles_and_permanent_access(world, base_clock, clock):
    temp(Client(YM), world.uid("thu_mgr"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00")
    clock.set(at("2026-10-21", "09:00"))
    assert board("thu.mgr@x.test", THU, world).status_code == 200
    assert Client("thu.mgr@x.test").get("/api/auth/me").json()["managed_weekdays"] == [4]


def test_a_permanent_and_a_temporary_manager_can_act_on_the_same_day(world, base_clock, clock):
    assert temp(Client(YM), world.uid("thu_mentor"), THU_WD, f"{THU}T08:00", f"{THU}T12:00").status_code == 201
    clock.set(at(THU, "09:00"))
    assert board("thu.mgr@x.test", THU, world).status_code == 200
    assert board("thu.mentor@x.test", THU, world).status_code == 200


def test_overlapping_temporary_appointments_are_refused_and_adjacent_ones_accepted(world, base_clock):
    ym = Client(YM)
    assert temp(ym, world.uid("thu_mgr"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00").status_code == 201
    assert temp(ym, world.uid("thu_mentor"), TUE_WD, f"{TUE}T10:00", f"{TUE}T14:00").json()["code"] == "temporary_overlap"
    assert temp(ym, world.uid("thu_mentor"), TUE_WD, f"{TUE}T12:00", f"{TUE}T14:00").status_code == 201  # adjacent
    assert temp(ym, world.uid("thu_mentor"), TUE_WD, f"{TUE}T06:00", f"{TUE}T08:00").status_code == 201  # adjacent


def test_concurrent_overlapping_creation_lets_only_one_through(world, base_clock):
    def attempt(user):
        return temp(Client(YM), world.uid(user), TUE_WD, f"{TUE}T16:00", f"{TUE}T18:00").status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(attempt, ["thu_mgr", "thu_mentor"]))
    assert codes == [201, 409]


def test_only_admin_and_youth_manager_create_edit_or_revoke(world, base_clock):
    for email in ("thu.mgr@x.test", "thu.youth@x.test"):
        assert temp(Client(email), world.uid("thu_mentor"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00").status_code == 403
    appt = temp(Client("admin@x.test"), world.uid("thu_mentor"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00").json()
    assert Client("thu.mgr@x.test").put(f"/api/day-appointments/temporary/{appt['id']}",
                                            {"weekday": TUE_WD, "starts": f"{TUE}T09:00", "ends": f"{TUE}T11:00"}).status_code == 403
    assert Client("thu.mgr@x.test").post(f"/api/day-appointments/temporary/{appt['id']}/revoke", {"confirm": True}).status_code == 403
    assert Client("admin@x.test").post(f"/api/day-appointments/temporary/{appt['id']}/revoke", {"confirm": True}).status_code == 200


def test_ineligible_volunteer_is_not_promoted(world, base_clock):
    r = temp(Client(YM), world.uid("thu_youth"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00")
    assert r.status_code == 409 and r.json()["code"] == "day_manager_must_be_mentor"


def test_times_are_asia_jerusalem_and_must_be_ordered(world, db, base_clock):
    r = temp(Client(YM), world.uid("thu_mgr"), TUE_WD, f"{TUE}T10:00", f"{TUE}T11:00")
    assert r.status_code == 201 and r.json()["starts"] == f"{TUE}T10:00" and r.json()["ends"] == f"{TUE}T11:00"
    stored = db.scalar(select(TemporaryDayManagerAppointment.starts_at).where(
        TemporaryDayManagerAppointment.id == r.json()["id"]))
    assert stored.tzinfo is not None and stored.astimezone(timezone.utc).hour == 7  # 10:00 IDT = 07:00 UTC
    assert temp(Client(YM), world.uid("thu_mentor"), TUE_WD, f"{TUE}T12:00", f"{TUE}T12:00").json()["code"] == "bad_interval"
    assert temp(Client(YM), world.uid("thu_mentor"), TUE_WD, f"{TUE}T14:00", f"{TUE}T13:00").json()["code"] == "bad_interval"
    assert temp(Client(YM), world.uid("thu_mentor"), TUE_WD, f"{TUE}T10:00+03:00", f"{TUE}T11:00").status_code in (400, 422)


def test_edit_and_revoke_are_recorded_and_history_is_kept(world, base_clock):
    ym = Client(YM)
    appt = temp(ym, world.uid("thu_mgr"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00").json()
    edited = ym.put(f"/api/day-appointments/temporary/{appt['id']}", {"weekday": TUE_WD, "starts": f"{TUE}T08:00", "ends": f"{TUE}T10:00"}).json()
    assert edited["ends"] == f"{TUE}T10:00" and edited["updated_by"] and edited["updated_at"]
    revoked = ym.post(f"/api/day-appointments/temporary/{appt['id']}/revoke", {"confirm": True}).json()
    assert revoked["status"] == "revoked" and revoked["revoked_by"] and revoked["revoked_at"]
    assert ym.put(f"/api/day-appointments/temporary/{appt['id']}", {"weekday": TUE_WD, "starts": f"{TUE}T08:00", "ends": f"{TUE}T09:00"}).json()["code"] == "appointment_revoked"
    assert ym.post(f"/api/day-appointments/temporary/{appt['id']}/revoke", {"confirm": False}).status_code == 400
    listed = [a for a in ym.get("/api/day-appointments/temporary").json() if a["id"] == appt["id"]]
    assert listed and listed[0]["status"] == "revoked" and listed[0]["created_by"]


def test_status_follows_time_scheduled_active_expired(world, base_clock, clock):
    appt = temp(Client(YM), world.uid("thu_mgr"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00").json()
    ym = Client(YM)
    def status():
        return next(a["status"] for a in ym.get("/api/day-appointments/temporary").json() if a["id"] == appt["id"])
    assert status() == "scheduled"
    clock.set(at(TUE, "09:00")); assert status() == "active"
    clock.set(at(TUE, "12:00")); assert status() == "expired"


def test_notifications_include_the_active_temporary_manager_without_duplicates(world, db, base_clock, clock):
    temp(Client(YM), world.uid("thu_mgr"), THU_WD, f"{THU}T08:00", f"{THU}T12:00")  # same person, same day
    temp(Client(YM), world.uid("thu_mentor"), TUE_WD, f"{TUE}T08:00", f"{TUE}T12:00")
    during = at(TUE, "09:00")
    recipients = day_manager_ids(db, TUE_WD, during)
    assert [str(x) for x in recipients] == [str(world.uid("thu_mentor"))]
    thursday = [str(x) for x in day_manager_ids(db, THU_WD, at(THU, "09:00"))]
    assert thursday.count(str(world.uid("thu_mgr"))) == 1  # permanent and temporary, one recipient
    assert [str(x) for x in day_manager_ids(db, TUE_WD, at(TUE, "13:00"))] != [str(world.uid("thu_mentor"))]  # expired


def test_the_youth_day_list_shows_the_temporary_manager_separately(world, base_clock, clock):
    temp(Client(YM), world.uid("thu_mentor"), THU_WD, f"{THU}T08:00", f"{THU}T12:00")
    clock.set(at(THU, "09:00"))
    body = Client(YM).get(f"/api/youth/days/{THU_WD}/volunteers").json()
    assert [m["user_id"] for m in body["day_managers"]] == [str(world.uid("thu_mgr"))]
    assert [t["user_id"] for t in body["temporary_managers"]] == [str(world.uid("thu_mentor"))]
