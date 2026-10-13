from datetime import timedelta

from sqlalchemy import select

from app.notifications import worker
from app.notifications.models import OutboxMessage
from tests.conftest import Client, at
from tests.helpers import assign, board, register, shift

THU = "2026-10-15"


def events(db, kind):
    db.expire_all()
    return list(db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == kind)))


def test_dirty_draft_idle_reminder_once_per_idle_period(world, clock, db):
    register(Client("thu.youth@x.test"), world.day(THU), evening=True)
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    assign(mgr, shift(b, "evening")["slots"][0]["slot_key"], world.uid("thu_youth"))
    t0 = clock.now()
    worker.run_once(t0 + timedelta(minutes=9, seconds=59))
    assert events(db, "draft_idle_reminder") == []
    worker.run_once(t0 + timedelta(minutes=10))
    worker.run_once(t0 + timedelta(minutes=25))  # still idle: no flood
    assert len(events(db, "draft_idle_reminder")) == 1
    # A new edit starts a new idle period -> exactly one more reminder.
    clock.set(t0 + timedelta(minutes=30))
    mgr.patch(f"/api/manage/slots/{shift(b, 'evening')['slots'][0]['slot_key']}", {"capacity": 2})
    worker.run_once(t0 + timedelta(minutes=41))
    reminders = events(db, "draft_idle_reminder")
    assert len(reminders) == 2 and {m.recipient_user_id for m in reminders} == {world.users["thu_mgr"].id}


def test_untouched_draft_does_not_remind(world, clock, db):
    board(Client("thu.mgr@x.test"), world.day(THU))
    worker.run_once(clock.now() + timedelta(hours=1))
    assert events(db, "draft_idle_reminder") == []


def test_publication_target_only_reminds_never_publishes(world, clock, db):
    board(Client("thu.mgr@x.test"), world.day(THU))
    def thursday_reminders():
        return [m for m in events(db, "publish_target_reminder") if m.payload["date"] == THU]

    worker.run_once(at("2026-10-14", "19:59"))
    assert thursday_reminders() == []
    worker.run_once(at("2026-10-14", "20:00"))
    worker.run_once(at("2026-10-14", "20:30"))
    assert [m.recipient_user_id for m in thursday_reminders()] == [world.users["thu_mgr"].id]
    db.refresh(world.days[THU])
    assert world.days[THU].latest_published_version_id is None


def test_registration_open_targets_eligible_people_and_no_closing_soon_reminder(world, clock, db):
    worker.run_once(at("2026-10-17", "20:01"))  # Thursday 22 Oct opens Sat 17 20:00
    opened = [m for m in events(db, "registration_opened") if m.payload["date"] == "2026-10-22"]
    thursday_people = {world.users[k].id for k in ("thu_mgr", "thu_mentor", "thu_youth", "thu_trainee", "thu_trainee2")}
    assert {m.recipient_user_id for m in opened} == thursday_people
    # Decision 2026-10-12: the "registration closing soon" reminder was removed entirely.
    for t in (at("2026-10-19", "18:30"), at("2026-10-20", "17:59")):
        worker.run_once(t)
    assert events(db, "registration_closing_soon") == []


def test_delivery_status_is_truthful_without_a_phone_channel(world, clock, db):
    worker.run_once(at("2026-10-17", "20:01"))
    db.expire_all()
    statuses = {m.status for m in db.scalars(select(OutboxMessage))}
    assert statuses == {"simulated"}  # never "accepted"/"delivered" without a provider


def test_provider_failures_retry_with_backoff_then_fail(world, clock, db, monkeypatch):
    class Broken(worker.PushChannel):
        name = "broken"

        def send(self, msg):
            raise RuntimeError("provider down")

    monkeypatch.setattr(worker, "get_channel", lambda: Broken())
    t = at("2026-10-17", "20:01")
    worker.run_once(t)
    db.expire_all()
    msg = db.scalars(select(OutboxMessage)).first()
    assert msg.status == "queued" and msg.attempts == 1 and msg.next_attempt_at > t
    for i in range(1, 10):
        worker.run_once(t + timedelta(hours=i))
    db.expire_all()
    msg = db.get(OutboxMessage, msg.id)
    assert msg.status == "failed" and msg.attempts == worker.MAX_ATTEMPTS and "provider down" in msg.last_error
