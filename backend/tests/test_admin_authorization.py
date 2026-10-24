"""Admin authorization: full access to every feature and every day, with no extra role, home day,
invitation or shift needed. Other roles stay restricted, and data-integrity rules still hold for Admin.

The 'solo admin' below has the admin role and nothing else: no home day, no day-manager scope, no
youth-manager role, no invitation and no shift. Only a volunteer level is set, because availability
and assignments feed pools that contain volunteers (a data rule, not an authorization rule)."""
from datetime import datetime

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.notifications.models import OutboxMessage
from tests.conftest import Client, at, make_user
from tests.helpers import assign, board, publish, register, shift
from tests.test_journal import FULL, THU, new_ride, publish_evening

FRI, MON, SAT = "2026-10-16", "2026-10-12", "2026-10-17"
THU_ARG = THU  # Thursday 15.10: home day of the thu_* volunteers, managed by thu_mgr


def solo_admin(db, world, email="solo.admin@x.test"):
    """Admin role only. Not a day manager, not a youth manager, not in any shift, no home day."""
    u = make_user(db, email, "mentor", None, at("2026-10-12", "12:00"), roles=("admin",))
    db.commit()
    c = Client(email)
    me = c.get("/api/auth/me").json()
    assert me["is_admin"] is True and me["managed_weekdays"] == [] and me["is_youth_manager"] is False
    assert me["home_weekday"] is None
    return c, u


# ---------- 1. full access to a day that is not the admin's own ----------

def test_admin_runs_a_day_that_is_not_their_home_day(world, db):
    admin, _ = solo_admin(db, world)
    b = board(admin, world.day(THU))  # Thursday: managed by thu_mgr, not by this admin
    ev, mo = shift(b, "evening"), shift(b, "morning")
    assert admin.post(f"/api/manage/slots/{ev['slots'][0]['slot_key']}/assign",
                      json={"user_id": world.uid("thu_youth")}).status_code == 201
    assert admin.post(f"/api/manage/shifts/{ev['id']}/participants", json={"user_id": world.uid("thu_trainee2")}).status_code == 201
    assert admin.post(f"/api/manage/shifts/{mo['id']}/registration", json={"open": True}).status_code == 200
    assert admin.put(f"/api/manage/shifts/{ev['id']}/order", json={"user_ids": [world.uid("thu_trainee2")]}).status_code == 200
    assert admin.patch(f"/api/days/{world.day(THU)}/timing",
                       json={"publish_target_at": "2026-10-14T20:00:00+03:00"}).status_code == 200
    assert admin.get(f"/api/manage/days/{world.day(THU)}/registrations").status_code == 200
    assert publish(admin, world.day(THU)).status_code == 200
    assert admin.get(f"/api/schedule/{world.day(THU)}").status_code == 200  # published view, no home day needed
    assert admin.get(f"/api/manage/days/{world.day(THU)}/board").status_code == 200


def test_admin_sees_every_day_in_the_manager_list(world, db):
    admin, _ = solo_admin(db, world)
    days = admin.get("/api/manage/days").json()
    days = days["items"] if isinstance(days, dict) else days
    assert {d["date"] for d in days} >= {MON, THU, FRI, SAT}


def test_admin_publishes_and_reads_schedules_of_every_day(world, db):
    admin, _ = solo_admin(db, world)
    for d in (THU, FRI, SAT):
        b = board(admin, world.day(d))
        assert publish(admin, world.day(d)).status_code == 200, d
    listed = {x["date"] for x in admin.get("/api/schedule").json()["items"]}
    assert {THU, FRI, SAT} <= listed


# ---------- 2. registration: no home day, no invitation, no shift ----------

def test_admin_answers_availability_on_any_day_without_invitation(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))  # Thursday registration is open
    admin, _ = solo_admin(db, world)
    assert THU in {r["date"] for r in admin.get("/api/registrations").json()}  # listed without home day
    ids = {s["kind"]: s["shift_id"] for s in admin.get(f"/api/registrations/{world.day(THU)}").json()["shifts"]}
    assert "evening" in ids
    assert register(admin, world.day(THU), evening=True).status_code == 200


def test_volunteer_without_home_day_or_invitation_still_cannot_register(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    mon_vol = Client("mon.vol@x.test")  # home day Monday
    assert THU not in {r["date"] for r in mon_vol.get("/api/registrations").json()}
    r = mon_vol.get(f"/api/registrations/{world.day(THU)}")
    assert r.status_code == 404 and r.json()["code"] == "day_not_found"


# ---------- 3. mentoring forms, trainee records, progress: outside every window ----------

def test_admin_documents_and_corrects_outside_mentor_windows(world, db, clock):
    publish_evening(world, THU)
    clock.set(at("2026-10-20", "12:00"))  # the Thursday evening window closed long ago
    admin, _ = solo_admin(db, world)
    trainee = world.uid("thu_trainee")
    mentor = Client("thu.mentor@x.test")
    # A mentor outside the window cannot even open the journal; admin can.
    assert mentor.get(f"/api/journal/trainees/{trainee}").status_code == 404
    j = admin.get(f"/api/journal/trainees/{trainee}")
    assert j.status_code == 200 and j.json()["access"]["can_manage"] is True
    ev = j.json()["shifts"][0]["shift_id"]
    rid, r = new_ride(admin, world, shift_id=ev)
    assert r.status_code == 201
    assert admin.put(f"/api/journal/rides/{rid}/description", {"description": "סחרחורת", "expected_version": 0}).status_code == 200
    assert admin.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "summary": "בדיקה", "expected_version": 0}).status_code == 200
    assert admin.put(f"/api/journal/summaries/{trainee}/{ev}",
                     {"strengths": "יוזמה", "improvements_none": True, "expected_version": 0}).status_code == 200
    assert admin.post(f"/api/journal/trainees/{trainee}/corrections", {"count_delta": 1, "reason": "תיקון ידני"}).status_code == 201
    assert admin.get(f"/api/journal/trainees/{trainee}/extensions").status_code == 200
    assert admin.get(f"/api/journal/trainees/{trainee}/notes").status_code == 200
    assert admin.get(f"/api/journal/trainees/{trainee}/progress").status_code == 200
    assert admin.post(f"/api/journal/rides/{rid}/cancel", {"reason": "בדיקת מנהל"}).status_code == 200
    assert admin.post(f"/api/journal/rides/{rid}/restore", {"reason": "בדיקת מנהל"}).status_code == 200
    # Admin reads access is audited (privacy rule for personal notes and journals).
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "admin_notes_read")) is not None


def test_admin_reopens_access_and_sees_every_trainee(world, db):
    publish_evening(world, THU)
    admin, _ = solo_admin(db, world)
    trainees = {t["id"] for t in admin.get("/api/journal/trainees").json()}
    assert world.uid("thu_trainee") in trainees and world.uid("mon_trainee") in trainees
    ext = admin.post(f"/api/journal/trainees/{world.uid('thu_trainee')}/extensions",
                     {"mentor_id": world.uid("thu_mentor"), "shift_id": next(iter(
                         s["shift_id"] for s in admin.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").json()["shifts"])),
                      "hours": 2})
    assert ext.status_code == 201


# ---------- 4. administration: approvals, roles, templates, audit ----------

def test_admin_runs_the_whole_admin_interface(world, db):
    admin, _ = solo_admin(db, world)
    assert admin.get("/api/admin/users").status_code == 200
    assert admin.get("/api/admin/outbox").status_code == 200
    assert admin.get("/api/admin/audit").status_code == 200
    assert admin.get(f"/api/admin/days/{world.day(THU)}").status_code == 200
    assert admin.get("/api/templates").status_code == 200
    assert admin.put("/api/templates/3", {"open_days_before": 5, "open_time": "20:00"}).status_code == 200  # any weekday
    assert admin.put("/api/day-managers/3", {"user_ids": [world.uid("thu_mentor")]}).status_code == 200
    assert admin.get("/api/day-managers").status_code == 200
    assert admin.post("/api/admin/days/generate", {"start": "2026-12-01", "end": "2026-12-02"}).status_code == 200
    assert admin.put(f"/api/manage/users/{world.uid('thu_youth')}/level", {"level": "youth"}).status_code == 200
    assert admin.patch(f"/api/admin/users/{world.uid('mon_vol')}", {"home_weekday": 4, "is_youth_manager": True}).status_code == 200
    assert admin.patch(f"/api/admin/users/{world.uid('mon_vol')}", {"is_youth_manager": False}).status_code == 200
    assert admin.get(f"/api/users/search?q=thu").status_code == 200


def test_admin_approves_pending_accounts_and_grants_roles(world, db):
    admin, _ = solo_admin(db, world)
    assert admin.post(f"/api/admin/users/{world.uid('pending')}/approve", {"level": "youth", "home_weekday": 2}).status_code == 200
    assert admin.patch(f"/api/admin/users/{world.uid('pending')}", {"is_admin": True}).status_code == 200
    assert Client("pending@x.test").get("/api/auth/me").json()["is_admin"] is True
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "role_grant")) is not None


def test_admin_notifications_and_publication_are_audited(world, db):
    admin, _ = solo_admin(db, world)
    b = board(admin, world.day(THU))
    assign(admin, shift(b, "evening")["slots"][0]["slot_key"], world.uid("thu_youth"))
    publish(admin, world.day(THU))
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "publish")) is not None
    assert db.query(OutboxMessage).count() > 0


# ---------- 5. other roles remain restricted ----------

def test_other_roles_remain_restricted(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    thu_mgr, thu_youth, trainee = Client("thu.mgr@x.test"), Client("thu.youth@x.test"), Client("thu.trainee@x.test")
    youthmgr, sat_mgr = Client("youthmgr@x.test"), Client("sat.mgr@x.test")
    # A day manager manages only their own weekday.
    assert thu_mgr.get(f"/api/manage/days/{world.day(FRI)}/board").status_code == 404
    assert thu_mgr.get(f"/api/manage/days/{world.day(FRI)}/registrations").status_code == 404
    assert sat_mgr.get(f"/api/manage/days/{world.day(THU)}/registrations").status_code == 404
    # A volunteer cannot manage days at all.
    assert thu_youth.post(f"/api/manage/days/{world.day(THU)}/draft").status_code == 404
    assert thu_youth.get(f"/api/admin/users").status_code == 404
    assert trainee.get("/api/admin/audit").status_code == 404
    # The youth manager is not an admin: no admin interface, no template or day-manager powers beyond scope.
    assert youthmgr.get("/api/admin/users").status_code == 404
    assert youthmgr.put("/api/templates/1", {"open_days_before": 5}).status_code == 403
    assert youthmgr.post(f"/api/admin/users/{world.uid('pending')}/approve", {"level": "youth"}).status_code == 404
    # Only the admin approves accounts or changes levels outside a manager's own day.
    assert thu_mgr.post(f"/api/admin/users/{world.uid('pending')}/approve", {"level": "youth"}).status_code == 404
    assert thu_mgr.put(f"/api/manage/users/{world.uid('mon_trainee')}/level", {"level": "youth"}).status_code == 403
    # A day manager cannot appoint day managers.
    assert thu_mgr.put("/api/day-managers/1", {"user_ids": []}).status_code == 404
    # A volunteer without a home day cannot register on Thursday (see also the earlier test).
    assert Client("mon.vol@x.test").get(f"/api/registrations/{world.day(THU)}").status_code == 404


def test_admin_grant_does_not_leak_to_other_accounts(world, db):
    admin, _ = solo_admin(db, world)
    assert Client("thu.mgr@x.test").get("/api/admin/users").status_code == 404
    assert Client("thu.mgr@x.test").get("/api/auth/me").json()["is_admin"] is False


# ---------- 6. integrity rules still apply to Admin ----------

def test_admin_still_cannot_double_book_or_break_trainee_rules(world, db):
    admin, _ = solo_admin(db, world)
    b = board(admin, world.day(THU))
    ev, re = shift(b, "evening"), shift(b, "reinforcement")
    slot_a, slot_b = ev["slots"][0]["slot_key"], ev["slots"][1]["slot_key"]
    assert assign(admin, slot_a, world.uid("thu_trainee")).status_code == 201
    r = assign(admin, slot_b, world.uid("thu_trainee"))  # same person, same shift, second ambulance
    assert r.status_code == 409 and r.json()["code"] == "already_in_shift"
    r = assign(admin, re["slots"][0]["slot_key"], world.uid("thu_trainee"))  # trainee: reinforcement + evening
    assert r.status_code == 409 and r.json()["code"] == "trainee_reinforcement_evening"


def test_admin_still_follows_schedule_and_registration_rules(world, db, clock):
    admin, _ = solo_admin(db, world)
    mo = shift(board(admin, world.day(SAT)), "morning")
    r = admin.post(f"/api/manage/shifts/{mo['id']}/registration", {"open": False})
    assert r.status_code == 409 and r.json()["code"] == "saturday_fixed"
    ev = shift(board(admin, world.day(THU)), "evening")
    assert admin.patch(f"/api/manage/slots/{ev['slots'][0]['slot_key']}", {"capacity": 4}).status_code == 400
    # The first publication of a day may be empty; republishing without any change is refused.
    b = board(admin, world.day(THU))
    assert publish(admin, world.day(THU)).status_code == 200
    board(admin, world.day(THU))  # a new working copy with no edits
    r = publish(admin, world.day(THU))
    assert r.status_code == 409 and r.json()["code"] == "nothing_to_publish"


def test_admin_still_cannot_appoint_a_non_mentor_as_day_manager(world, db):
    admin, _ = solo_admin(db, world)
    r = admin.put("/api/day-managers/1", {"user_ids": [world.uid("mon_vol")]})
    assert r.status_code == 409 and r.json()["code"] == "day_manager_must_be_mentor"
    assert admin.put("/api/day-managers/1", {"user_ids": [world.uid("mon_trainee")]}).status_code == 409


def test_admin_cannot_document_a_ride_for_a_non_trainee_or_edit_a_cancelled_one(world, db, clock):
    publish_evening(world, THU)
    clock.set(at(THU, "17:00"))
    admin, _ = solo_admin(db, world)
    ev = next(s["shift_id"] for s in admin.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").json()["shifts"])
    rid, r = new_ride(admin, world, trainee="thu_youth", shift_id=ev)  # youth volunteer, not a trainee
    assert r.status_code == 403 and r.json()["code"] == "not_trainee"
    rid, r = new_ride(admin, world, shift_id=ev)
    assert r.status_code == 201
    admin.post(f"/api/journal/rides/{rid}/cancel", {"reason": "בדיקה"})
    r = admin.put(f"/api/journal/rides/{rid}/description", {"description": "x", "expected_version": 0})
    assert r.status_code == 409 and r.json()["code"] == "ride_cancelled"


def test_admin_cannot_remove_the_last_admin_or_deactivate_them(world, db):
    admin, u = solo_admin(db, world)
    fixture_admin = Client("admin@x.test")
    # Two admins exist in the fixture; remove the other one first, then the last one is protected.
    assert admin.patch(f"/api/admin/users/{world.uid('admin')}", {"is_admin": False}).status_code == 200
    r = admin.patch(f"/api/admin/users/{u.id}", {"is_admin": False})
    assert r.status_code == 409 and r.json()["code"] == "last_admin"
    assert admin.patch(f"/api/admin/users/{u.id}", {"status": "inactive"}).status_code == 409
