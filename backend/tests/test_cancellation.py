"""Volunteer cancellation (decision 2026-10-16). Before the shift starts a volunteer may cancel their own
registration or assignment with no approval. After publication the live schedule and their card update at
once and the Day Manager gets one warning. At or after the shift start the server refuses, even when the
screen was opened earlier. A cancellation never edits the published version (it is history)."""
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select

from app.days.models import ActivityDay, Shift
from app.notifications.models import OutboxMessage
from app.scheduling.models import Assignment
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, register, shift

THU = "2026-10-15"
BEFORE_PUBLICATION = "2026-10-12"  # registration open, nothing published


def evening_row(db) -> Shift:
    return db.scalar(select(Shift).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                     .where(ActivityDay.local_date == date.fromisoformat(THU), Shift.kind == "evening"))


def cancel(client: Client, shift_id):
    return client.post(f"/api/shifts/{shift_id}/cancel", {})


def warnings(db):
    db.expire_all()
    return list(db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "volunteer_cancelled")))


def live_schedule_text(world) -> str:
    r = Client("thu.mgr@x.test").get(f"/api/schedule/{world.day(THU)}")
    assert r.status_code == 200, r.text
    return r.text


def publish_with(world, volunteers, clock_at=BEFORE_PUBLICATION):
    """Registers the given volunteers for the evening, assigns the first to the first slot, publishes."""
    mgr = Client("thu.mgr@x.test")
    for key in volunteers:
        assert register(Client(world.users[key].email), world.day(THU), evening=True).status_code == 200
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], str(world.uid(volunteers[0])))
    r = publish(mgr, world.day(THU))
    assert r.status_code == 200, r.text
    return r.json()["published_version_id"]


def test_before_publication_cancel_withdraws_registration_without_approval(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    vol = Client("thu.youth@x.test")
    assert register(vol, world.day(THU), evening=True).status_code == 200
    r = cancel(vol, evening_row(db).id)
    assert r.status_code == 200, r.text
    assert r.json() == {"withdrawn": True, "published_cancelled": False, "removed_from_draft": False}
    assert warnings(db) == []  # nothing was published, so the Day Manager is not warned
    assert cancel(vol, evening_row(db).id).status_code == 404  # nothing left to cancel


def test_after_publication_cancel_updates_schedule_card_and_warns_manager_once(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    version_id = publish_with(world, ["thu_youth"])
    youth = world.uid("thu_youth")
    assert str(youth) in live_schedule_text(world)
    assert Client("thu.youth@x.test").get("/api/me/next-assignment").json()["assignment"] is not None

    r = cancel(Client("thu.youth@x.test"), evening_row(db).id)
    assert r.status_code == 200 and r.json()["published_cancelled"] is True, r.text

    # The live schedule and the volunteer's own card change at once.
    assert str(youth) not in live_schedule_text(world)
    assert Client("thu.youth@x.test").get("/api/me/next-assignment").json()["assignment"] is None
    # The published version is history: its assignment row is untouched.
    assert db.scalar(select(func.count()).select_from(Assignment).where(
        Assignment.version_id == version_id, Assignment.user_id == youth)) == 1
    # Exactly one warning, to the Day Manager of that weekday.
    msgs = warnings(db)
    assert len(msgs) == 1 and msgs[0].recipient_user_id == uuid.UUID(str(world.uid("thu_mgr")))
    # Cancelling again is refused (nothing live to cancel) and does not warn a second time.
    assert cancel(Client("thu.youth@x.test"), evening_row(db).id).status_code == 404
    assert len(warnings(db)) == 1


def test_cancel_is_refused_at_and_after_shift_start_even_if_screen_was_open(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    publish_with(world, ["thu_youth", "thu_trainee"])
    start = evening_row(db).starts_at
    vol = Client("thu.trainee@x.test")
    clock.set(start)  # the start instant itself: cancellation closed
    r = cancel(vol, evening_row(db).id)
    assert r.status_code == 409 and r.json()["code"] == "shift_started", r.text
    clock.set(start + timedelta(hours=1))
    assert cancel(vol, evening_row(db).id).json()["code"] == "shift_started"
    # The refused cancel changed nothing: the assigned volunteer is still on the live schedule.
    assert str(world.uid("thu_youth")) in live_schedule_text(world)
    assert warnings(db) == []


def test_cancel_is_allowed_one_second_before_start(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    publish_with(world, ["thu_youth"])
    clock.set(evening_row(db).starts_at - timedelta(seconds=1))
    r = cancel(Client("thu.youth@x.test"), evening_row(db).id)
    assert r.status_code == 200 and r.json()["published_cancelled"] is True, r.text


def test_cancelled_volunteer_is_not_carried_into_draft_and_not_republished(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    publish_with(world, ["thu_youth"])
    mgr = Client("thu.mgr@x.test")
    draft_id = board(mgr, world.day(THU))["draft"]["id"]  # working copy made before the cancellation
    youth = world.uid("thu_youth")
    assert db.scalar(select(func.count()).select_from(Assignment).where(
        Assignment.version_id == uuid.UUID(draft_id), Assignment.user_id == youth)) == 1

    assert cancel(Client("thu.youth@x.test"), evening_row(db).id).json()["removed_from_draft"] is True
    assert db.scalar(select(func.count()).select_from(Assignment).where(
        Assignment.version_id == uuid.UUID(draft_id), Assignment.user_id == youth)) == 0

    # Publishing again does not bring the volunteer back.
    assert publish(mgr, world.day(THU)).status_code in (200, 409)
    assert str(youth) not in live_schedule_text(world)


def test_cancel_before_publication_removes_person_from_pre_publication_draft(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    mgr = Client("thu.mgr@x.test")
    assert register(Client("thu.youth@x.test"), world.day(THU), evening=True).status_code == 200
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], str(world.uid("thu_youth")))
    r = cancel(Client("thu.youth@x.test"), evening_row(db).id)
    assert r.json() == {"withdrawn": True, "published_cancelled": False, "removed_from_draft": True}
    assert warnings(db) == []


def choice_row(db, world, user_key: str, shift_id):
    from app.registration.models import Registration, RegistrationChoice
    db.expire_all()
    return db.execute(select(RegistrationChoice.available, RegistrationChoice.cancelled_at, RegistrationChoice.cancelled_by_id)
                      .join(Registration, Registration.id == RegistrationChoice.registration_id)
                      .where(Registration.user_id == world.uid(user_key),
                             Registration.activity_day_id == uuid.UUID(world.day(THU)),
                             RegistrationChoice.shift_id == shift_id)).one_or_none()


def board_shift(world, mgr_email="thu.mgr@x.test"):
    r = Client(mgr_email).get(f"/api/manage/days/{world.day(THU)}/board")
    assert r.status_code == 200, r.text
    return shift(r.json(), "evening")


def test_cancellation_is_an_explicit_state_not_not_available(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    vol = Client("thu.youth@x.test")
    assert register(vol, world.day(THU), evening=True).status_code == 200
    sid = evening_row(db).id
    assert cancel(vol, sid).status_code == 200

    # The original answer stays on the record; the cancellation is recorded beside it, by whom and when.
    row = choice_row(db, world, "thu_youth", sid)
    assert row.available is True and row.cancelled_at is not None
    assert row.cancelled_by_id == uuid.UUID(str(world.uid("thu_youth")))

    # The volunteer sees "בוטל", not "not available".
    mine = [s for s in vol.get(f"/api/registrations/{world.day(THU)}").json()["shifts"] if s["shift_id"] == str(sid)][0]
    assert mine["cancelled_at"] is not None and mine["available"] is True

    # The Day Manager sees the cancellation, and the person is not an eligible candidate.
    ev = board_shift(world)
    assert str(world.uid("thu_youth")) in [c["user_id"] for c in ev["cancelled"]]
    assert str(world.uid("thu_youth")) not in [w["user_id"] for w in ev["waiting"]]


def test_not_available_is_distinct_from_cancelled(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    vol = Client("thu.trainee@x.test")
    assert register(vol, world.day(THU), evening=False).status_code == 200  # answered: not coming
    sid = evening_row(db).id
    row = choice_row(db, world, "thu_trainee", sid)
    assert row.available is False and row.cancelled_at is None  # "לא מגיע/ה", not "בוטל"
    ev = board_shift(world)
    assert str(world.uid("thu_trainee")) not in [c["user_id"] for c in ev["cancelled"]]
    # Nothing was cancelled, so there is nothing to cancel.
    assert cancel(vol, sid).status_code == 404


def test_a_new_answer_after_cancelling_reopens_the_registration(world, db, clock):
    clock.set(at(BEFORE_PUBLICATION, "21:00"))
    vol = Client("thu.youth@x.test")
    assert register(vol, world.day(THU), evening=True).status_code == 200
    sid = evening_row(db).id
    assert cancel(vol, sid).status_code == 200
    assert register(vol, world.day(THU), evening=True).status_code == 200
    row = choice_row(db, world, "thu_youth", sid)
    assert row.available is True and row.cancelled_at is None
    ev = board_shift(world)
    assert str(world.uid("thu_youth")) in [w["user_id"] for w in ev["waiting"]]
    assert str(world.uid("thu_youth")) not in [c["user_id"] for c in ev["cancelled"]]
