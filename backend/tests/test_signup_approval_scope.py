"""Day Managers approve pending signups for their own weekday only; the scope holds in the list and in the approve
action, including a direct request with another applicant's id; temporary managers only inside their interval."""
from sqlalchemy import select

from app.accounts.models import AuthorityRole, User
from app.accounts.security import hash_password
from app.accounts.service import current_home_weekday, current_level
from tests.conftest import Client, at, make_user

THU_MGR, YM = "thu.mgr@x.test", "youthmgr@x.test"


def applicant(db, now, email, requested_weekday):
    u = make_user(db, email, None, None, now, status="pending")
    u.requested_weekday = requested_weekday
    db.commit()
    return u


def approve(client, user_id, level="youth"):
    return client.post(f"/api/manage/signups/{user_id}/approve", {"level": level})


def pending_ids(client):
    r = client.get("/api/manage/signups")
    assert r.status_code == 200, r.text
    return {x["id"] for x in r.json()}


def test_day_manager_sees_and_approves_applicants_for_the_managed_day(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    now = clock.now()
    thu = applicant(db, now, "signup.thu@x.test", 4)
    mgr = Client(THU_MGR)
    assert str(thu.id) in pending_ids(mgr)
    r = approve(mgr, thu.id, level="youth")
    assert r.status_code == 200, r.text
    db.expire_all()
    fresh = db.get(User, thu.id)
    assert fresh.status == "active" and fresh.approved_by_id == world.users["thu_mgr"].id
    assert current_home_weekday(db, thu.id) == 4          # the requested day becomes the home day
    assert current_level(db, thu.id) == "youth"
    assert db.scalars(select(AuthorityRole).where(AuthorityRole.user_id == thu.id)).all() == []  # no role granted


def test_cross_day_approval_is_refused_and_the_applicant_stays_pending(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    now = clock.now()
    mon = applicant(db, now, "signup.mon@x.test", 1)
    mgr = Client(THU_MGR)
    assert str(mon.id) not in pending_ids(mgr)                       # not listed
    r = approve(mgr, mon.id)                                         # direct request with another applicant's id
    assert r.status_code == 404 and r.json()["code"] == "signup_not_found"
    db.expire_all()
    assert db.get(User, mon.id).status == "pending"
    assert current_home_weekday(db, mon.id) is None
    # The manager cannot send a home day or a role, or change the requested day.
    r = mgr.post(f"/api/manage/signups/{mon.id}/approve", {"level": "youth", "home_weekday": 1})
    assert r.status_code == 422
    r = mgr.post(f"/api/manage/signups/{mon.id}/approve", {"level": "youth", "requested_weekday": 4})
    assert r.status_code == 422
    r = mgr.post(f"/api/manage/signups/{mon.id}/approve", {"level": "admin"})
    assert r.status_code == 422


def test_requested_weekday_grants_no_access_before_approval(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    now = clock.now()
    applicant(db, now, "signup.pending@x.test", 4)
    me = Client("signup.pending@x.test")
    assert me.get("/api/registrations").status_code in (401, 403)
    assert me.get("/api/manage/signups").status_code in (401, 403, 404)


def test_temporary_manager_approves_only_inside_the_appointment_interval(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    now = clock.now()
    mon = applicant(db, now, "signup.tempmon@x.test", 1)
    # thu.mentor (a mentor) receives a Monday appointment for 12.10 20:00 to 14.10 12:00 (Asia/Jerusalem).
    r = Client(YM).post("/api/day-appointments/temporary", {"user_id": str(world.uid("thu_mentor")), "weekday": 1,
                                                            "starts": "2026-10-12T20:00", "ends": "2026-10-14T12:00"})
    assert r.status_code == 201, r.text
    temp = Client("thu.mentor@x.test")
    assert str(mon.id) in pending_ids(temp)
    assert approve(temp, mon.id).status_code == 200

    other = applicant(db, now, "signup.tempmon2@x.test", 1)
    clock.set(at("2026-10-14", "12:00"))  # the appointment has ended
    # After the interval the mentor manages no weekday: the list and the approve action are both closed.
    assert temp.get("/api/manage/signups").status_code == 404
    assert approve(temp, other.id).status_code == 404
    db.expire_all()
    assert db.get(User, other.id).status == "pending"


def test_admin_keeps_full_access_and_youth_manager_keeps_no_approval_right(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    now = clock.now()
    thu = applicant(db, now, "signup.admin@x.test", 4)
    assert str(thu.id) in pending_ids(Client("admin@x.test"))
    assert approve(Client("youthmgr@x.test"), thu.id).status_code == 404  # no approval right, as before
    assert Client("youthmgr@x.test").post(f"/api/admin/users/{thu.id}/approve",
                                          {"level": "youth", "home_weekday": 4}).status_code == 404  # existing admin-only gate
    # Admin's existing route still works for any applicant.
    assert Client("admin@x.test").post(f"/api/admin/users/{thu.id}/approve",
                                       {"level": "youth", "home_weekday": 4}).status_code == 200
