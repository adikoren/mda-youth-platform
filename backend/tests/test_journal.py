import threading
import uuid

import pytest
from sqlalchemy import select, text

from app.audit.models import AuditEvent
from app.notifications import worker
from app.notifications.models import OutboxMessage
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, shift

THU, NEXT_THU, SAT = "2026-10-15", "2026-10-22", "2026-10-17"
FULL = {"blood_pressure": "success", "pulse": "partial", "breathing": "not_performed", "forms": "failure"}


def publish_evening(world, date, slot1=("thu_mentor", "thu_mgr", "thu_trainee"), slot2=("thu_youth", "thu_trainee2")):
    """Publish an evening crew: slot 1 has formal mentors + trainee, slot 2 has no formal mentor."""
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(date))
    ev = shift(b, "evening")
    for key in slot1:
        assert assign(mgr, ev["slots"][0]["slot_key"], world.uid(key)).status_code == 201
    for key in slot2:
        assert assign(mgr, ev["slots"][1]["slot_key"], world.uid(key)).status_code == 201
    assert publish(mgr, world.day(date)).status_code == 200
    return ev["id"]


@pytest.fixture
def evening(world, clock):
    return publish_evening(world, THU)


def new_ride(client, world, trainee="thu_trainee", shift_id=None, ride_id=None, description=None):
    rid = ride_id or str(uuid.uuid4())
    body = {"id": rid, "trainee_id": world.uid(trainee), "shift_id": shift_id}
    if description is not None:
        body["description"] = description
    return rid, client.post("/api/journal/rides", body)


# ---------------- access windows ----------------

def test_evening_mentor_window_is_1330_to_1000_next_day(world, clock, evening):
    mentor = Client("thu.mentor@x.test")
    url = f"/api/journal/trainees/{world.uid('thu_trainee')}"
    for when, expected in ((at(THU, "13:29"), 404), (at(THU, "13:30"), 200), (at("2026-10-16", "09:59"), 200),
                           (at("2026-10-16", "10:00"), 404)):
        clock.set(when)
        assert mentor.get(url).status_code == expected, when
    clock.set(at(THU, "13:30"))
    listed = mentor.get("/api/journal/mentor").json()
    assert [x["full_name"] for x in listed] == ["thu.trainee"]


def test_only_formal_mentors_of_the_same_ambulance(world, clock, evening):
    clock.set(at(THU, "18:00"))
    mentor = Client("thu.mentor@x.test")
    assert mentor.get(f"/api/journal/trainees/{world.uid('thu_trainee2')}").status_code == 404  # other ambulance
    youth = Client("thu.youth@x.test")  # youth volunteers never get mentoring access
    assert youth.get(f"/api/journal/trainees/{world.uid('thu_trainee2')}").status_code == 404
    assert youth.get("/api/journal/mentor").json() == []


def test_morning_shifts_grant_no_mentor_access(world, clock):
    mgr = Client("sat.mgr@x.test")
    b = board(mgr, world.day(SAT))
    key = shift(b, "morning")["slots"][0]["slot_key"]
    assign(mgr, key, world.uid("sat_mgr"))
    assign(mgr, key, world.uid("mon_trainee"))
    publish(mgr, world.day(SAT))
    clock.set(at(SAT, "14:00"))
    assert Client("sat.mgr@x.test").get(f"/api/journal/trainees/{world.uid('mon_trainee')}").status_code == 404
    # ...but the trainee can still document the morning shift themselves.
    _, r = new_ride(Client("mon.trainee@x.test"), world, "mon_trainee", shift(b, "morning")["id"], description="סחרחורת")
    assert r.status_code == 201


def test_draft_assignment_never_grants_mentor_access(world, clock):
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    key = shift(b, "evening")["slots"][0]["slot_key"]
    assign(mgr, key, world.uid("thu_mentor"))
    assign(mgr, key, world.uid("thu_trainee"))
    clock.set(at(THU, "18:00"))
    assert Client("thu.mentor@x.test").get(f"/api/journal/trainees/{world.uid('thu_trainee')}").status_code == 404


def test_mentor_reads_case_history_but_not_previous_shift_summaries(world, clock, evening):
    mentor = Client("thu.mentor@x.test")
    clock.set(at(THU, "16:00"))
    rid, _ = new_ride(mentor, world, shift_id=evening)
    assert mentor.put(f"/api/journal/rides/{rid}/assessment",
                      {"markers": FULL, "summary": "טיפול טוב", "expected_version": 0}).status_code == 200
    r = mentor.put(f"/api/journal/summaries/{world.uid('thu_trainee')}/{evening}",
                   {"strengths": "תקשורת", "improvements_none": True, "expected_version": 0})
    assert r.status_code == 200

    next_shift = publish_evening(world, NEXT_THU, slot1=("thu_mentor", "thu_trainee"), slot2=())
    clock.set(at(NEXT_THU, "16:00"))
    j = mentor.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").json()
    assert [r["assessment"]["summary"] for r in j["rides"]] == ["טיפול טוב"]  # historical per-case record
    assert [s["shift_id"] for s in j["shifts"]] == [next_shift]  # only the current shift's summary slot
    assert all(s["summary"] is None for s in j["shifts"])
    # Editing the old shift's assessment or summary is not allowed.
    assert mentor.put(f"/api/journal/rides/{rid}/assessment",
                      {"markers": FULL, "summary": "x", "expected_version": 1}).status_code == 403
    assert mentor.put(f"/api/journal/summaries/{world.uid('thu_trainee')}/{evening}",
                      {"strengths_none": True, "improvements_none": True, "expected_version": 1}).status_code == 403
    # The trainee and the home-day manager still see the old summary.
    for email in ("thu.trainee@x.test", "thu.mgr@x.test"):
        shifts = Client(email).get(f"/api/journal/trainees/{world.uid('thu_trainee')}").json()["shifts"]
        old = next(s for s in shifts if s["shift_id"] == evening)
        assert old["summary"]["strengths"] == "תקשורת"


def test_youth_manager_and_guest_day_manager_see_progress_only(world, clock, evening):
    clock.set(at(THU, "16:00"))
    for email in ("youthmgr@x.test", "sat.mgr@x.test"):
        c = Client(email)
        assert c.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").status_code == 404
        assert c.get(f"/api/journal/trainees/{world.uid('thu_trainee')}/progress").json()["ride_count"] == 0
    assert Client("thu.youth@x.test").get(f"/api/journal/trainees/{world.uid('thu_trainee')}/progress").status_code == 404


def test_home_day_transfer_moves_manager_journal_access(world, clock, evening):
    clock.set(at("2026-10-20", "12:00"))
    mgr = Client("thu.mgr@x.test")
    url = f"/api/journal/trainees/{world.uid('thu_trainee')}"
    assert mgr.get(url).status_code == 200
    admin = Client("admin@x.test")
    assert admin.patch(f"/api/admin/users/{world.uid('thu_trainee')}", {"home_weekday": 1}).status_code == 200
    assert mgr.get(url).status_code == 404


# ---------------- rides: creation, parts, idempotency, conflicts ----------------

def test_either_party_creates_first_and_parts_are_separate(world, clock, evening):
    clock.set(at(THU, "17:00"))
    mentor, trainee = Client("thu.mentor@x.test"), Client("thu.trainee@x.test")
    rid, r = new_ride(mentor, world, shift_id=evening, description="ignored for mentors")
    assert r.status_code == 201 and r.json()["description"] is None
    mine = trainee.get("/api/journal/me").json()
    assert mine["rides"][0]["awaiting_description"] is True
    # Parts have independent versions: the description and the assessment never overwrite each other.
    assert trainee.put(f"/api/journal/rides/{rid}/description", {"description": "סחרחורת", "expected_version": 0}).status_code == 200
    assert mentor.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "summary": "s", "expected_version": 0}).status_code == 200
    assert trainee.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "expected_version": 1}).status_code == 403
    assert mentor.put(f"/api/journal/rides/{rid}/description", {"description": "x", "expected_version": 1}).status_code == 403
    ride = trainee.get("/api/journal/me").json()["rides"][0]
    assert ride["description"] == "סחרחורת" and ride["assessment"]["markers"] == FULL


def test_create_is_idempotent_and_counts_once(world, clock, evening):
    clock.set(at(THU, "17:00"))
    trainee = Client("thu.trainee@x.test")
    rid, first = new_ride(trainee, world, shift_id=evening, description="כאב ראש")
    _, again = new_ride(trainee, world, shift_id=evening, ride_id=rid, description="כאב ראש")
    assert first.status_code == 201 and again.status_code == 200 and again.json()["seq"] == first.json()["seq"]
    # Same id from the mentor for the same trainee/shift: returns the saved ride, no duplicate.
    _, mentor_retry = new_ride(Client("thu.mentor@x.test"), world, shift_id=evening, ride_id=rid)
    assert mentor_retry.status_code == 200
    # Same id reused for a different trainee is a conflict, never a silent success.
    _, clash = new_ride(Client("admin@x.test"), world, trainee="thu_trainee2", shift_id=evening, ride_id=rid)
    assert clash.status_code == 409
    assert trainee.get("/api/journal/me").json()["progress"]["ride_count"] == 1


def test_concurrent_retries_of_one_offline_draft_create_one_ride(world, clock, evening, engine):
    clock.set(at(THU, "17:00"))
    rid = str(uuid.uuid4())
    clients = [Client("thu.trainee@x.test") for _ in range(3)]
    barrier, codes = threading.Barrier(3), []

    def go(c):
        barrier.wait()
        codes.append(new_ride(c, world, shift_id=evening, ride_id=rid, description="נפילה")[1].status_code)

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 200, 201]
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from rides")).scalar() == 1


def test_sequence_is_unique_per_trainee_under_concurrency(world, clock, evening, engine):
    clock.set(at(THU, "17:00"))
    clients = [Client("thu.trainee@x.test") for _ in range(4)]
    barrier = threading.Barrier(4)

    def go(c):
        barrier.wait()
        new_ride(c, world, shift_id=evening, description="מקרה")

    threads = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with engine.connect() as conn:
        seqs = sorted(r[0] for r in conn.execute(text("select seq from rides")))
    assert seqs == [1, 2, 3, 4]


def test_two_mentors_both_have_access_and_stale_writes_conflict(world, clock, evening, db):
    clock.set(at(THU, "17:00"))
    a, b = Client("thu.mentor@x.test"), Client("thu.mgr@x.test")
    rid, _ = new_ride(a, world, shift_id=evening)
    assert a.put(f"/api/journal/rides/{rid}/assessment", {"markers": {"pulse": "success"}, "expected_version": 0}).status_code == 200
    stale = b.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "summary": "x", "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["current"]["markers"]["pulse"] == "success"
    assert b.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "summary": "x", "expected_version": 1}).status_code == 200
    contributors = a.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").json()["rides"][0]["contributors"]
    assert contributors == ["thu.mentor", "thu.mgr"]


def test_markers_validate_four_choices_and_unselected(world, clock, evening):
    clock.set(at(THU, "17:00"))
    mentor = Client("thu.mentor@x.test")
    rid, _ = new_ride(mentor, world, shift_id=evening)
    bad = mentor.put(f"/api/journal/rides/{rid}/assessment", {"markers": {"pulse": "great"}, "expected_version": 0})
    assert bad.status_code == 400
    bad = mentor.put(f"/api/journal/rides/{rid}/assessment", {"markers": {"temperature": "success"}, "expected_version": 0})
    assert bad.status_code == 400
    r = mentor.put(f"/api/journal/rides/{rid}/assessment",
                   {"markers": {"pulse": "not_performed", "forms": None}, "expected_version": 0})
    assert r.json()["markers"] == {"blood_pressure": None, "pulse": "not_performed", "breathing": None, "forms": None}


def test_sync_after_window_closed_is_refused_and_home_manager_can_reopen(world, clock, evening):
    clock.set(at("2026-10-16", "10:00"))
    mentor = Client("thu.mentor@x.test")
    rid, r = new_ride(mentor, world, shift_id=evening)
    assert r.status_code == 404  # window closed: the client keeps its local draft
    # A guest-day manager cannot reopen; the trainee's current home-day manager can.
    body = {"mentor_id": world.uid("thu_mentor"), "shift_id": evening, "hours": 2}
    assert Client("sat.mgr@x.test").post(f"/api/journal/trainees/{world.uid('thu_trainee')}/extensions", body).status_code == 403
    assert Client("thu.mgr@x.test").post(f"/api/journal/trainees/{world.uid('thu_trainee')}/extensions", body).status_code == 201
    _, r = new_ride(mentor, world, shift_id=evening, ride_id=rid)
    assert r.status_code == 201
    clock.set(at("2026-10-16", "12:00"))
    assert mentor.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").status_code == 404
    # Admin override also works.
    assert Client("admin@x.test").post(f"/api/journal/trainees/{world.uid('thu_trainee')}/extensions", body).status_code == 201
    assert mentor.get(f"/api/journal/trainees/{world.uid('thu_trainee')}").status_code == 200


def test_mentor_with_readable_journal_but_closed_shift_gets_explicit_window_message(world, clock, evening):
    publish_evening(world, NEXT_THU, slot1=("thu_mentor", "thu_trainee"), slot2=())
    clock.set(at(NEXT_THU, "16:00"))
    mentor = Client("thu.mentor@x.test")
    rid, r = new_ride(mentor, world, shift_id=evening)  # old shift, journal readable via the new shift
    assert r.status_code == 403 and r.json()["code"] == "mentor_window_closed"


# ---------------- completeness, reminders ----------------

def completeness_of(client, world, trainee, shift_id):
    j = client.get(f"/api/journal/trainees/{world.uid(trainee)}").json()
    return next(s for s in j["shifts"] if s["shift_id"] == shift_id)["completeness"]


def test_completeness_rules(world, clock, evening):
    clock.set(at(THU, "17:00"))
    mentor, trainee = Client("thu.mentor@x.test"), Client("thu.trainee@x.test")
    rid, _ = new_ride(trainee, world, shift_id=evening, description="סחרחורת")
    c = completeness_of(mentor, world, "thu_trainee", evening)
    assert c == {"mentor_required": True, "trainee_missing": 0, "mentor_missing_rides": 1, "summary_missing": True,
                 "complete": False}
    partial = {**FULL, "forms": None}  # one action not selected yet
    mentor.put(f"/api/journal/rides/{rid}/assessment", {"markers": partial, "summary": "ok", "expected_version": 0})
    assert completeness_of(mentor, world, "thu_trainee", evening)["mentor_missing_rides"] == 1
    mentor.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "summary": "ok", "expected_version": 1})
    assert completeness_of(mentor, world, "thu_trainee", evening)["mentor_missing_rides"] == 0
    r = mentor.put(f"/api/journal/summaries/{world.uid('thu_trainee')}/{evening}",
                   {"strengths": "x", "strengths_none": True, "expected_version": 0})
    assert r.status_code == 400  # text OR "no comments", not both
    mentor.put(f"/api/journal/summaries/{world.uid('thu_trainee')}/{evening}",
               {"strengths_none": True, "improvements": "לשים לב לטפסים", "expected_version": 0})
    assert completeness_of(mentor, world, "thu_trainee", evening)["complete"] is True


def test_no_formal_mentor_requires_trainee_part_only(world, clock, evening):
    clock.set(at(THU, "17:00"))
    trainee2 = Client("thu.trainee2@x.test")
    new_ride(trainee2, world, "thu_trainee2", evening, description="קוצר נשימה")
    c = completeness_of(trainee2, world, "thu_trainee2", evening)
    assert c["mentor_required"] is False and c["complete"] is True


def reminders(db, kind):
    db.expire_all()
    return list(db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == kind)))


def test_reminders_at_shift_end_and_0900_only_while_incomplete(world, clock, evening, db):
    clock.set(at(THU, "17:00"))
    mentor = Client("thu.mentor@x.test")
    rid, _ = new_ride(mentor, world, shift_id=evening)  # trainee description missing, mentor part missing
    new_ride(Client("thu.trainee2@x.test"), world, "thu_trainee2", evening)  # no formal mentor, no description
    worker.run_once(at(THU, "22:59"))
    assert reminders(db, "journal_reminder_trainee") == [] and reminders(db, "journal_reminder_mentor") == []
    worker.run_once(at(THU, "23:00"))
    worker.run_once(at(THU, "23:30"))  # same phase: no duplicates
    t = reminders(db, "journal_reminder_trainee")
    m = reminders(db, "journal_reminder_mentor")
    assert {x.recipient_user_id for x in t} == {world.users["thu_trainee"].id, world.users["thu_trainee2"].id}
    # Both formal mentors of the first ambulance; nobody for the mentor-less trainee.
    assert {x.recipient_user_id for x in m} == {world.users["thu_mentor"].id, world.users["thu_mgr"].id}
    assert all(x.payload["trainee_id"] == world.uid("thu_trainee") for x in m)

    # The trainee completes their part before the morning; mentors do not.
    clock.set(at(THU, "23:40"))
    Client("thu.trainee@x.test").put(f"/api/journal/rides/{rid}/description", {"description": "התעלפות", "expected_version": 0})
    worker.run_once(at("2026-10-16", "09:00"))
    t2 = [x for x in reminders(db, "journal_reminder_trainee") if x.recipient_user_id == world.users["thu_trainee"].id]
    assert len(t2) == 1  # no morning reminder for completed content
    assert len(reminders(db, "journal_reminder_mentor")) == 4  # morning reminder for the mentors' missing part

    # Complete everything: no further reminders.
    clock.set(at("2026-10-16", "09:10"))
    mentor.put(f"/api/journal/rides/{rid}/assessment", {"markers": FULL, "summary": "טוב", "expected_version": 0})
    mentor.put(f"/api/journal/summaries/{world.uid('thu_trainee')}/{evening}",
               {"strengths_none": True, "improvements_none": True, "expected_version": 0})
    db.execute(text("delete from notification_outbox where event_type like 'journal_reminder_%'"))
    db.commit()
    worker.run_once(at("2026-10-16", "09:30"))
    assert [x.recipient_user_id for x in reminders(db, "journal_reminder_mentor")] == []


# ---------------- progress ----------------

def test_progress_counts_server_rides_once_and_corrections_are_audited(world, clock, evening, db):
    clock.set(at(THU, "17:00"))
    trainee = Client("thu.trainee@x.test")
    for _ in range(2):
        new_ride(trainee, world, shift_id=evening, description="מקרה")
    p = trainee.get("/api/journal/me").json()["progress"]
    assert p == {"ride_count": 2, "last_ride_date": THU}
    # A later published shift without rides does not move the last ride date.
    publish_evening(world, NEXT_THU, slot1=("thu_mentor", "thu_trainee"), slot2=())
    clock.set(at("2026-10-23", "12:00"))
    assert trainee.get("/api/journal/me").json()["progress"]["last_ride_date"] == THU
    # Corrections: current home-day manager only.
    body = {"count_delta": -1, "reason": "נסיעה כפולה"}
    assert Client("sat.mgr@x.test").post(f"/api/journal/trainees/{world.uid('thu_trainee')}/corrections", body).status_code == 403
    r = Client("thu.mgr@x.test").post(f"/api/journal/trainees/{world.uid('thu_trainee')}/corrections", body)
    assert r.status_code == 201 and r.json()["ride_count"] == 1
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "progress_correction")) is not None
    # Progress is shown on the manager board as context.
    b = Client("thu.mgr@x.test").get(f"/api/manage/days/{world.day(NEXT_THU)}/board").json()
    crew = shift(b, "evening")["slots"][0]["crew"]
    assert next(p for p in crew if p["user_id"] == world.uid("thu_trainee"))["progress"]["ride_count"] == 1


# ---------------- personal notes ----------------

def test_personal_notes_privacy_and_sharing(world, clock, evening, db):
    clock.set(at(THU, "23:30"))
    trainee = Client("thu.trainee@x.test")
    private = trainee.post("/api/notes", {"content": "הרגשתי לחץ", "shared": False}).json()
    mgr, admin, mentor = Client("thu.mgr@x.test"), Client("admin@x.test"), Client("thu.mentor@x.test")
    url = f"/api/journal/trainees/{world.uid('thu_trainee')}/notes"
    assert mgr.get(url).json() == []  # no hint that a private note exists
    assert [n["content"] for n in admin.get(url).json()] == ["הרגשתי לחץ"]
    assert mentor.get(url).status_code == 404
    shared = trainee.put(f"/api/notes/{private['id']}", {"shared": True, "expected_version": 1}).json()
    assert [n["content"] for n in mgr.get(url).json()] == ["הרגשתי לחץ"]
    trainee.put(f"/api/notes/{private['id']}", {"shared": False, "expected_version": shared["version"]})
    assert mgr.get(url).json() == []
    # Note audit rows are admin-only; mentoring summaries are a different object.
    vis = {v for (v,) in db.execute(select(AuditEvent.visibility).where(AuditEvent.object_type == "personal_note"))}
    assert vis == {"admin_only"}
    assert Client("thu.youth@x.test").post("/api/notes", {"content": "x"}).status_code == 403


def test_notification_status_reports_no_phone_channel(world):
    assert Client("thu.trainee@x.test").get("/api/me/notification-status").json() == {"phone_channel_connected": False}
