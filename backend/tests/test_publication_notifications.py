"""Publication notification recipients (decision 2026-10-12, DESIGN_REVIEW §10.7):
assigned AND waiting volunteers of each affected shift, one message per person per publication,
and only after a manual publication — never for draft edits."""
from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select

from app.notifications.models import OutboxMessage
from tests.conftest import Client, at, make_user
from tests.helpers import assign, board, publish, register, shift

THU = "2026-10-15"
SAT = "2026-10-17"
SCHEDULE_EVENTS = ("assigned", "unassigned", "schedule_changed", "schedule_changed_waiting", "new_ambulance",
                   "schedule_published")


def schedule_messages(db, version_id=None) -> list[OutboxMessage]:
    q = select(OutboxMessage).where(OutboxMessage.event_type.in_(SCHEDULE_EVENTS))
    if version_id is not None:
        q = q.where(OutboxMessage.dedup_key.like(f"publication:{version_id}:%"))
    db.expire_all()
    return list(db.scalars(q))


def by_user(messages) -> dict:
    return {m.recipient_user_id: m for m in messages}


def changes(m: OutboxMessage) -> set[tuple[str, str]]:
    return {(x["shift"], x["change"]) for x in m.payload["shifts"]}


def extra_thursday_volunteers(db, clock, n=2):
    now = clock.now()
    users = [make_user(db, f"thu.extra{i}@x.test", "youth", 4, now) for i in range(n)]
    db.commit()
    return users


# ---------- waiting recipients ----------

def test_republication_notifies_assigned_and_waiting_of_the_affected_shift(world, db, clock):
    extra, added = extra_thursday_volunteers(db, clock)
    for key in ("thu_youth", "thu_mentor", "thu_trainee"):
        assert register(Client(world.users[key].email), world.day(THU), evening=True).status_code == 200
    register(Client(extra.email), world.day(THU), evening=True)
    register(Client(extra.email), world.day(THU), evening=False)  # withdrew before any assignment
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    # Manager-added participant (did not register) is part of the evening waiting list.
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/participants", {"user_id": str(added.id)}).status_code == 201
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_youth"))
    first = publish(mgr, world.day(THU)).json()

    first_msgs = by_user(schedule_messages(db, first["published_version_id"]))
    # First publication: the manager-added waiting person hears that the schedule was published.
    assert first_msgs[added.id].event_type == "schedule_published"
    assert extra.id not in first_msgs  # withdrawn availability = not waiting

    board(mgr, world.day(THU))
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_mentor"))
    second = publish(mgr, world.day(THU)).json()
    msgs = by_user(schedule_messages(db, second["published_version_id"]))
    U = world.users
    assert msgs[U["thu_mentor"].id].event_type == "assigned"
    assert msgs[U["thu_youth"].id].event_type == "schedule_changed"
    assert msgs[U["thu_trainee"].id].event_type == "schedule_changed_waiting"
    assert msgs[added.id].event_type == "schedule_changed_waiting"
    # Reinforcement was not affected by this publication, so the trainee (who is also in the
    # reinforcement pool through evening availability) is told about the evening only.
    assert changes(msgs[U["thu_trainee"].id]) == {("evening", "waiting")}
    # Not in the pool (never registered / withdrew / other day): nothing.
    assert set(msgs) == {U["thu_mentor"].id, U["thu_youth"].id, U["thu_trainee"].id, added.id}
    assert second["notifications_queued"] == 4

    # Waiting volunteers see the message in their own feed; text is Hebrew.
    feed = Client("thu.trainee@x.test").get("/api/me/notifications").json()
    waiting_note = next(n for n in feed if n["event_type"] == "schedule_changed_waiting")
    assert "ממתין" in waiting_note["text"] and waiting_note["phone_status"] == "queued"
    # Someone who withdrew before any assignment is not on the waiting list and gets nothing.
    assert Client(extra.email).get("/api/me/notifications").json() == []


def test_new_ambulance_reaches_waiting_and_removed_person_gets_only_unassigned(world, db):
    for key in ("thu_youth", "thu_mentor", "thu_trainee"):
        register(Client(world.users[key].email), world.day(THU), evening=True)
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_youth"))
    publish(mgr, world.day(THU))

    board(mgr, world.day(THU))
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/slots", {"capacity": 3}).status_code == 201
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/unassign", {"user_id": world.uid("thu_youth")}).status_code == 200
    r = publish(mgr, world.day(THU)).json()
    msgs = by_user(schedule_messages(db, r["published_version_id"]))
    U = world.users
    assert msgs[U["thu_mentor"].id].event_type == "new_ambulance"
    assert changes(msgs[U["thu_mentor"].id]) == {("evening", "waiting_new_ambulance")}
    # thu_youth is back in the pool but gets one message: the removal, with no schedule references.
    removed = msgs[U["thu_youth"].id]
    assert removed.event_type == "unassigned"
    assert set(removed.payload) == {"date", "shifts"}


# ---------- duplicate prevention ----------

def test_one_message_per_person_per_publication_across_shifts_and_retries(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))  # Saturday registration is open
    for email in ("mon.vol@x.test", "thu.youth@x.test"):
        assert register(Client(email), world.day(SAT), morning=True, evening=True).status_code == 200
    mgr = Client("sat.mgr@x.test")
    b = board(mgr, world.day(SAT))
    morning, evening = shift(b, "morning"), shift(b, "evening")
    assign(mgr, evening["slots"][0]["slot_key"], world.uid("mon_vol"))
    first = publish(mgr, world.day(SAT)).json()
    msgs = schedule_messages(db, first["published_version_id"])
    assert Counter(m.recipient_user_id for m in msgs).most_common(1)[0][1] == 1
    m = by_user(msgs)
    U = world.users
    # Assigned to evening and waiting for morning: one message, the most personal type wins.
    assert m[U["mon_vol"].id].event_type == "assigned"
    assert changes(m[U["mon_vol"].id]) == {("evening", "assigned"), ("morning", "published_waiting")}
    assert m[U["thu_youth"].id].event_type == "schedule_published"
    assert changes(m[U["thu_youth"].id]) == {("evening", "published_waiting"), ("morning", "published_waiting")}

    # Change both shifts in one publication.
    board(mgr, world.day(SAT))
    assign(mgr, morning["slots"][0]["slot_key"], world.uid("thu_youth"))
    assert mgr.post(f"/api/manage/shifts/{evening['id']}/slots", {"capacity": 3}).status_code == 201
    draft = mgr.get(f"/api/manage/days/{world.day(SAT)}/board").json()["draft"]
    body = {"draft_id": draft["id"], "expected_edit_count": draft["edit_count"]}
    second = mgr.post(f"/api/manage/days/{world.day(SAT)}/publish", body).json()
    msgs = schedule_messages(db, second["published_version_id"])
    assert sorted(Counter(m.recipient_user_id for m in msgs).values()) == [1, 1]
    m = by_user(msgs)
    assert m[U["thu_youth"].id].event_type == "assigned"
    assert changes(m[U["thu_youth"].id]) == {("morning", "assigned"), ("evening", "waiting_new_ambulance")}
    assert m[U["mon_vol"].id].event_type == "schedule_changed"
    assert changes(m[U["mon_vol"].id]) == {("evening", "changed"), ("morning", "waiting")}

    # A retried publish request adds nothing.
    total = db.scalar(select(func.count()).select_from(OutboxMessage))
    again = mgr.post(f"/api/manage/days/{world.day(SAT)}/publish", body).json()
    assert again["already_published"] is True
    assert db.scalar(select(func.count()).select_from(OutboxMessage)) == total

    # The database also refuses a second row for the same person and publication.
    keys = [m.dedup_key for m in schedule_messages(db)]
    assert len(keys) == len(set(keys))


def test_trainee_waiting_for_evening_and_reinforcement_gets_one_message(world, db):
    register(Client("thu.trainee@x.test"), world.day(THU), evening=True)
    register(Client("thu.youth@x.test"), world.day(THU), evening=True)
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    assign(mgr, shift(b, "evening")["slots"][0]["slot_key"], world.uid("thu_youth"))
    publish(mgr, world.day(THU))
    board(mgr, world.day(THU))
    assign(mgr, shift(b, "reinforcement")["slots"][0]["slot_key"], world.uid("thu_mentor"))
    assert mgr.post(f"/api/manage/shifts/{shift(b, 'evening')['id']}/slots", {"capacity": 3}).status_code == 201
    r = publish(mgr, world.day(THU)).json()
    mine = [m for m in schedule_messages(db, r["published_version_id"])
            if m.recipient_user_id == world.users["thu_trainee"].id]
    assert len(mine) == 1
    assert changes(mine[0]) == {("evening", "waiting_new_ambulance"), ("reinforcement", "waiting")}


# ---------- draft isolation ----------

def test_draft_edits_never_notify_and_only_real_changes_do_after_publication(world, db, clock):
    extra, = extra_thursday_volunteers(db, clock, n=1)
    for key in ("thu_youth", "thu_mentor", "thu_trainee"):
        register(Client(world.users[key].email), world.day(THU), evening=True)
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    ev = shift(b, "evening")
    slot_a, slot_b = ev["slots"][0]["slot_key"], ev["slots"][1]["slot_key"]
    # Before any publication, every kind of draft edit queues nothing schedule-related.
    assign(mgr, slot_a, world.uid("thu_youth"))
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/participants", {"user_id": str(extra.id)}).status_code == 201
    assert schedule_messages(db) == []
    publish(mgr, world.day(THU))
    after_first = len(schedule_messages(db))
    assert after_first > 0

    board(mgr, world.day(THU))
    assign(mgr, slot_b, world.uid("thu_mentor"))
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/move",
                    {"user_id": world.uid("thu_mentor"), "to_slot_key": slot_a}).status_code == 200
    assert mgr.patch(f"/api/manage/slots/{slot_b}", {"capacity": 2}).status_code == 200
    assert mgr.post(f"/api/manage/slots/{slot_b}/cancel").status_code == 200
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/slots", {"capacity": 2}).status_code == 201
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/unassign", {"user_id": world.uid("thu_youth")}).status_code == 200
    clock.set(clock.now() + timedelta(minutes=30))  # an idle draft only reminds the manager; it never notifies volunteers
    assert len(schedule_messages(db)) == after_first
    for email in ("thu.youth@x.test", "thu.mentor@x.test", "thu.trainee@x.test", extra.email):
        feed = Client(email).get("/api/me/notifications").json()
        assert all(n["event_type"] != "schedule_changed_waiting" for n in feed)

    # Undo every effective change, then publish: the draft was edited, but nothing changed for
    # anyone, so no schedule notifications are sent.
    assert mgr.post(f"/api/manage/slots/{slot_b}/restore").status_code == 200
    assert mgr.patch(f"/api/manage/slots/{slot_b}", {"capacity": 3}).status_code == 200
    assign(mgr, slot_a, world.uid("thu_youth"))
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/unassign", {"user_id": world.uid("thu_mentor")}).status_code == 200
    new_slot = [s for s in shift(mgr.get(f"/api/manage/days/{world.day(THU)}/board").json(), "evening")["slots"]
                if s["slot_key"] not in (slot_a, slot_b, *[x["slot_key"] for x in ev["slots"]])][0]
    assert mgr.post(f"/api/manage/slots/{new_slot['slot_key']}/cancel").status_code == 200
    r = publish(mgr, world.day(THU)).json()
    assert r["already_published"] is False
    assert schedule_messages(db, r["published_version_id"]) == []
