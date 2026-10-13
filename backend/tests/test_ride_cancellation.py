"""Audited cancellation of duplicate rides, and the access-end hint used to bound offline copies."""
from datetime import datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditEvent
from tests.conftest import Client, at
from tests.test_journal import FULL, THU, evening, new_ride  # noqa: F401  (evening is a fixture)


def journal(client, world, trainee="thu_trainee"):
    r = client.get(f"/api/journal/trainees/{world.uid(trainee)}")
    assert r.status_code == 200, r.text
    return r.json()


def two_rides_for_one_case(world, clock, evening):  # noqa: F811
    """The same case documented twice (e.g. on two phones while offline)."""
    clock.set(at(THU, "17:00"))
    trainee = Client("thu.trainee@x.test")
    original, r1 = new_ride(trainee, world, shift_id=evening, description="סחרחורת")
    duplicate, r2 = new_ride(trainee, world, shift_id=evening, description="סחרחורת")
    assert r1.status_code == r2.status_code == 201
    assert trainee.get("/api/journal/me").json()["progress"]["ride_count"] == 2
    return trainee, original, duplicate


def test_home_day_manager_cancels_duplicate_with_audit_and_progress_drops(world, clock, evening, db):  # noqa: F811
    trainee, original, duplicate = two_rides_for_one_case(world, clock, evening)
    body = {"reason": "תועדה פעמיים", "duplicate_of_ride_id": original}
    # Only the trainee's current home-day manager or an admin may cancel.
    for email in ("thu.trainee@x.test", "thu.mentor@x.test", "sat.mgr@x.test", "youthmgr@x.test"):
        r = Client(email).post(f"/api/journal/rides/{duplicate}/cancel", body)
        assert r.status_code in (403, 404), (email, r.status_code)
    r = Client("thu.mgr@x.test").post(f"/api/journal/rides/{duplicate}/cancel", body)
    assert r.status_code == 200 and r.json()["already_cancelled"] is False
    assert r.json()["progress"]["ride_count"] == 1
    # Repeating the request (retry / double tap) changes nothing and adds no audit row.
    again = Client("thu.mgr@x.test").post(f"/api/journal/rides/{duplicate}/cancel", body)
    assert again.status_code == 200 and again.json()["already_cancelled"] is True
    audits = db.scalars(select(AuditEvent).where(AuditEvent.action == "ride_cancel")).all()
    assert len(audits) == 1 and str(audits[0].object_id) == duplicate
    assert "reason" not in (audits[0].meta or {})  # free text stays on the ride, not in the log
    assert audits[0].meta["duplicate_of"] == original

    # The trainee sees why it was cancelled; the count is server-derived.
    mine = trainee.get("/api/journal/me").json()
    assert mine["progress"]["ride_count"] == 1
    cancelled = next(x for x in mine["rides"] if x["id"] == duplicate)
    original_seq = next(x for x in mine["rides"] if x["id"] == original)["seq"]
    assert cancelled["cancelled"]["reason"] == "תועדה פעמיים"
    assert cancelled["cancelled"]["duplicate_of_seq"] == original_seq
    assert cancelled["can_edit_description"] is False
    # Mentors in their window do not see cancelled rides at all.
    assert [x["id"] for x in journal(Client("thu.mentor@x.test"), world)["rides"]] == [original]


def test_cancelled_ride_refuses_edits_and_offline_retries_do_not_revive_it(world, clock, evening):  # noqa: F811
    trainee, original, duplicate = two_rides_for_one_case(world, clock, evening)
    mentor = Client("thu.mentor@x.test")
    Client("admin@x.test").post(f"/api/journal/rides/{duplicate}/cancel", {"reason": "כפולה"})  # admin override
    r = trainee.put(f"/api/journal/rides/{duplicate}/description", {"description": "x", "expected_version": 1})
    assert r.status_code == 409 and r.json()["code"] == "ride_cancelled"
    r = mentor.put(f"/api/journal/rides/{duplicate}/assessment", {"markers": FULL, "summary": "s", "expected_version": 0})
    assert r.status_code == 409 and r.json()["code"] == "ride_cancelled"
    # An offline create retry with the same id returns the cancelled ride; it is not recreated or counted.
    _, retry = new_ride(trainee, world, shift_id=evening, ride_id=duplicate, description="סחרחורת")
    assert retry.status_code == 200 and retry.json()["cancelled"] is True and retry.json()["created"] is False
    assert trainee.get("/api/journal/me").json()["progress"]["ride_count"] == 1


def test_cancelled_ride_does_not_count_as_missing_documentation(world, clock, evening):  # noqa: F811
    clock.set(at(THU, "17:00"))
    mentor = Client("thu.mentor@x.test")
    rid, _ = new_ride(mentor, world, shift_id=evening)  # created by mentor: awaiting description
    comp = next(s for s in journal(mentor, world)["shifts"] if s["shift_id"] == evening)["completeness"]
    assert comp["trainee_missing"] == 1 and comp["mentor_missing_rides"] == 1
    Client("thu.mgr@x.test").post(f"/api/journal/rides/{rid}/cancel", {"reason": "נפתחה בטעות"})
    comp = next(s for s in journal(mentor, world)["shifts"] if s["shift_id"] == evening)["completeness"]
    assert comp["trainee_missing"] == 0 and comp["mentor_missing_rides"] == 0


def test_restore_is_audited_and_counts_again(world, clock, evening, db):  # noqa: F811
    trainee, original, duplicate = two_rides_for_one_case(world, clock, evening)
    mgr = Client("thu.mgr@x.test")
    mgr.post(f"/api/journal/rides/{duplicate}/cancel", {"reason": "כפולה", "duplicate_of_ride_id": original})
    assert mgr.post(f"/api/journal/rides/{duplicate}/restore", {"reason": "x"}).status_code == 422
    r = mgr.post(f"/api/journal/rides/{duplicate}/restore", {"reason": "לא הייתה כפולה"})
    assert r.status_code == 200 and r.json()["cancelled"] is False and r.json()["progress"]["ride_count"] == 2
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "ride_restore")) is not None
    assert trainee.put(f"/api/journal/rides/{duplicate}/description",
                       {"description": "סחרחורת קלה", "expected_version": 1}).status_code == 200


def test_cancellation_validation_and_database_constraints(world, clock, evening, engine):  # noqa: F811
    trainee, original, duplicate = two_rides_for_one_case(world, clock, evening)
    mgr = Client("thu.mgr@x.test")
    assert mgr.post(f"/api/journal/rides/{duplicate}/cancel", {"reason": ""}).status_code == 422
    r = mgr.post(f"/api/journal/rides/{duplicate}/cancel", {"reason": "כפולה", "duplicate_of_ride_id": duplicate})
    assert r.status_code == 400
    other, _ = new_ride(Client("admin@x.test"), world, trainee="thu_trainee2", shift_id=evening)
    r = mgr.post(f"/api/journal/rides/{duplicate}/cancel", {"reason": "כפולה", "duplicate_of_ride_id": other})
    assert r.status_code == 400  # the original must be in the same trainee's journal
    mgr.post(f"/api/journal/rides/{original}/cancel", {"reason": "כפולה"})
    r = mgr.post(f"/api/journal/rides/{duplicate}/cancel", {"reason": "כפולה", "duplicate_of_ride_id": original})
    assert r.status_code == 409 and r.json()["code"] == "duplicate_of_cancelled"
    # The database refuses a half-recorded cancellation even outside the API.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE rides SET cancelled_at = now() WHERE id = :id"), {"id": duplicate})


def test_mentor_access_until_bounds_the_offline_copy(world, clock, evening):  # noqa: F811
    clock.set(at(THU, "17:00"))
    mentor = Client("thu.mentor@x.test")
    until = journal(mentor, world)["access"]["mentor_access_until"]
    assert list(until) == [evening]
    assert datetime.fromisoformat(until[evening]) == at("2026-10-16", "10:00")
    assert journal(Client("thu.trainee@x.test"), world)["access"]["mentor_access_until"] == {}
    # A reopened access extends the bound to the extension's end.
    clock.set(at("2026-10-16", "11:00"))
    Client("thu.mgr@x.test").post(f"/api/journal/trainees/{world.uid('thu_trainee')}/extensions",
                                  {"mentor_id": world.uid("thu_mentor"), "shift_id": evening, "hours": 3})
    extended = journal(mentor, world)["access"]["mentor_access_until"][evening]
    assert datetime.fromisoformat(extended) == at("2026-10-16", "14:00")
