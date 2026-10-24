"""Written requests to the day manager after registration closed.

Rules tested: a request is created only by an explicit submit, only in the closed window (after the
planning cutoff, before the date ends), retries with the same id create nothing new, the request text
is visible to the day's managers only, and opening the registration screen changes nothing."""
import uuid

from sqlalchemy import func, select, text

from app.notifications.models import OutboxMessage
from tests.conftest import Client, at
from tests.helpers import board, register, shift

THU = "2026-10-15"
CLOSED = "2026-10-14"  # Thursday registration closed on 13.10 at 18:00; the date has not ended yet


def counts(db):
    return (db.scalar(select(func.count()).select_from(OutboxMessage)),
            db.scalar(text("select count(*) from registration_requests")),
            db.scalar(text("select count(*) from audit_events where action = 'registration_request'")))


def evening_shift(world):
    return shift(board(Client("thu.mgr@x.test"), world.day(THU)), "evening")["id"]


def submit(client, world, message="אני לא יכול להגיע, אפשר להחליף?", request_id=None, shift_id=None):
    rid = request_id or str(uuid.uuid4())
    body = {"id": rid, "message": message, "shift_id": shift_id or evening_shift(world)}
    return rid, client.post(f"/api/registrations/{world.day(THU)}/requests", body)


def test_opening_the_closed_screen_creates_nothing(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")  # eligible: Thursday is the home day
    before = counts(db)
    r = vol.get(f"/api/registrations/{world.day(THU)}")
    assert r.status_code == 200 and r.json()["window"] == "closed_late_allowed"
    assert vol.get("/api/registrations").status_code == 200
    assert counts(db) == before  # no request, no notification, no audit row from merely opening


def test_explicit_submit_in_closed_window_notifies_the_day_manager_once(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")
    rev = vol.get(f"/api/registrations/{world.day(THU)}").json()["revision"]
    rid, r = submit(vol, world)
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["status"] == "pending"
    assert counts(db)[1] == 1
    msgs = db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "registration_request")).all()
    assert [m.recipient_user_id for m in msgs] == [world.users["thu_mgr"].id]  # the day's manager only
    assert "message" not in msgs[0].payload and rid in str(msgs[0].payload)  # ids only, never the text
    # The registration itself is untouched.
    assert vol.get(f"/api/registrations/{world.day(THU)}").json()["revision"] == rev


def test_retry_with_the_same_id_creates_nothing_new(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")
    rid, first = submit(vol, world)
    before = counts(db)
    _, again = submit(vol, world, request_id=rid)
    assert first.status_code == 201 and again.status_code == 200 and again.json()["created"] is False
    assert counts(db) == before  # no second row, no second notification
    # Another request from the same person is a new request.
    _, other = submit(vol, world, message="שאלה נוספת")
    assert other.status_code == 201 and counts(db)[1] == 2


def test_a_reused_id_from_someone_else_is_refused(world, clock):
    clock.set(at(CLOSED, "12:00"))
    rid, _ = submit(Client("thu.youth@x.test"), world)
    _, r = submit(Client("thu.trainee@x.test"), world, request_id=rid)
    assert r.status_code == 409 and r.json()["code"] == "request_id_conflict"


def test_submit_is_refused_outside_the_closed_window(world, db, clock):
    vol = Client("thu.youth@x.test")
    clock.set(at("2026-10-09", "12:00"))  # before the registration opens
    _, r = submit(vol, world)
    assert r.status_code == 409 and r.json()["code"] == "registration_not_open"
    clock.set(at("2026-10-12", "21:00"))  # still open: answer directly instead
    _, r = submit(vol, world)
    assert r.status_code == 409 and r.json()["code"] == "registration_open"
    clock.set(at("2026-10-16", "09:00"))  # the date has ended
    _, r = submit(vol, world)
    assert r.status_code == 409 and r.json()["code"] == "shift_ended"  # the shift ended first
    assert counts(db)[1] == 0


def test_volunteer_without_access_to_the_day_cannot_submit(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    mon_vol = Client("mon.vol@x.test")  # home day Monday, no invitation to Thursday
    assert mon_vol.get(f"/api/registrations/{world.day(THU)}").status_code == 404
    _, r = submit(mon_vol, world)
    assert r.status_code == 404 and counts(db)[1] == 0


def test_empty_and_oversized_messages_are_refused(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")
    _, r = submit(vol, world, message="   ")
    assert r.status_code == 400 and r.json()["code"] == "request_empty"
    _, r = submit(vol, world, message="א" * 1001)
    assert r.status_code == 422
    assert counts(db)[1] == 0


def test_only_the_day_managers_see_and_resolve_requests(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    rid, _ = submit(Client("thu.youth@x.test"), world, message="בקשת בדיקה")
    mgr = Client("thu.mgr@x.test")
    listed = mgr.get(f"/api/manage/days/{world.day(THU)}/requests").json()
    assert [(x["id"], x["message"]) for x in listed] == [(rid, "בקשת בדיקה")]
    assert [m["event_type"] for m in mgr.get("/api/me/notifications").json()] == ["registration_request"]
    # Other managers, the volunteers and the youth manager (no day scope) do not see the text.
    assert Client("sat.mgr@x.test").get(f"/api/manage/days/{world.day(THU)}/requests").status_code == 404
    assert Client("thu.youth@x.test").get(f"/api/manage/days/{world.day(THU)}/requests").status_code == 404
    assert Client("sat.mgr@x.test").post(f"/api/manage/requests/{rid}/decision", {"decision": "approved"}).status_code == 404
    # Resolving marks it handled; it stays listed for review (not deleted).
    assert mgr.post(f"/api/manage/requests/{rid}/decision", {"decision": "approved"}).status_code == 200
    listed = mgr.get(f"/api/manage/days/{world.day(THU)}/requests").json()
    assert [(x["id"], x["status"]) for x in listed] == [(rid, "approved")] and listed[0]["resolved_at"]
    assert db.scalar(text(f"select status from registration_requests where id = '{rid}'")) == "approved"


def test_admin_can_submit_but_cannot_register_directly_after_closing(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    admin = Client("admin@x.test")  # eligible on any day, no invitation needed
    _, r = submit(admin, world)
    assert r.status_code == 201
    assert register(admin, world.day(THU), evening=True).status_code == 409  # Admin: same rules as volunteers


def test_the_screen_shows_sent_requests_so_reloading_does_not_duplicate(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")
    assert vol.get(f"/api/registrations/{world.day(THU)}").json()["requests"] == []
    rid, _ = submit(vol, world)
    got = vol.get(f"/api/registrations/{world.day(THU)}").json()["requests"]
    assert [(x["id"], x["status"]) for x in got] == [(rid, "pending")]
    # Another volunteer's requests are never shown.
    assert Client("thu.trainee@x.test").get(f"/api/registrations/{world.day(THU)}").json()["requests"] == []


def test_identical_pending_request_is_refused_until_confirmed(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")
    _, first = submit(vol, world, message="אפשר להחליף משמרת?")
    before = counts(db)
    # An accidental second press (new form, same text, still pending) creates nothing.
    _, dup = submit(vol, world, message="  אפשר   להחליף משמרת? ")
    assert dup.status_code == 409 and dup.json()["code"] == "duplicate_request"
    assert dup.json()["existing_id"] == first.json()["id"]
    assert counts(db) == before
    # The volunteer confirms: an intentional repeat is accepted.
    _, ok = submit(vol, world, message="אפשר להחליף משמרת?", request_id=str(uuid.uuid4()))
    assert ok.status_code == 409  # still refused without the flag
    rid = str(uuid.uuid4())
    r = vol.post(f"/api/registrations/{world.day(THU)}/requests",
                 {"id": rid, "message": "אפשר להחליף משמרת?", "shift_id": evening_shift(world), "confirm_duplicate": True})
    assert r.status_code == 201 and counts(db)[1] == 2


def test_identical_text_is_allowed_again_once_the_first_is_handled(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol, mgr = Client("thu.youth@x.test"), Client("thu.mgr@x.test")
    rid, _ = submit(vol, world, message="בקשה חוזרת")
    assert mgr.post(f"/api/manage/requests/{rid}/decision", {"decision": "approved"}).status_code == 200
    _, again = submit(vol, world, message="בקשה חוזרת")  # a new issue, after the first was handled
    assert again.status_code == 201


def test_a_different_volunteer_with_the_same_text_is_not_a_duplicate(world, clock):
    clock.set(at(CLOSED, "12:00"))
    _, a = submit(Client("thu.youth@x.test"), world, message="שאלה משותפת")
    _, b = submit(Client("thu.trainee@x.test"), world, message="שאלה משותפת")
    assert a.status_code == 201 and b.status_code == 201
