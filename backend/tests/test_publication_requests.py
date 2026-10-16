"""Publication and late-registration requests: a pending request blocks publication until the Day Manager
approves or rejects it; a decision changes no registration or assignment; no request can join a schedule
after publication; a request racing a publication cannot both succeed."""
import threading
import uuid

from sqlalchemy import func, select, text

from app.scheduling.models import Assignment
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, shift

THU = "2026-10-15"
CLOSED = "2026-10-14"  # registration closed, schedule not published, shift not started


def evening_id(world):
    return shift(board(Client("thu.mgr@x.test"), world.day(THU)), "evening")["id"]


def submit(client, world, text_="בקשה להצטרף למשמרת"):
    rid = str(uuid.uuid4())
    return rid, client.post(f"/api/registrations/{world.day(THU)}/requests",
                            {"id": rid, "message": text_, "shift_id": evening_id(world)})


def decide(world, rid, decision):
    return Client("thu.mgr@x.test").post(f"/api/manage/requests/{rid}/decision", {"decision": decision})


def test_pending_request_blocks_publication_until_decided(world, clock):
    clock.set(at(CLOSED, "12:00"))
    rid, r = submit(Client("thu.youth@x.test"), world)
    assert r.status_code == 201
    mgr = Client("thu.mgr@x.test")
    blocked = publish(mgr, world.day(THU))
    assert blocked.status_code == 409 and blocked.json()["code"] == "pending_requests"
    assert "בקשות להרשמה" in blocked.json()["message"] and "לא ניתן לפרסם" in blocked.json()["message"]
    # Viewing the request is not a decision.
    mgr.get(f"/api/manage/days/{world.day(THU)}/requests")
    assert publish(mgr, world.day(THU)).status_code == 409
    # Rejecting unblocks publication (and a rejection assigns nobody).
    assert decide(world, rid, "rejected").status_code == 200
    assert publish(mgr, world.day(THU)).status_code == 200


def test_approval_unblocks_publication_and_assigns_nobody(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    vol = Client("thu.youth@x.test")
    rid, _ = submit(vol, world)
    rows_before = db.scalar(select(func.count()).select_from(Assignment))
    assert decide(world, rid, "approved").status_code == 200
    # Approval records a decision only: no assignment, and the registration is unchanged.
    assert db.scalar(select(func.count()).select_from(Assignment)) == rows_before
    assert db.scalar(text(f"select status from registration_requests where id = '{rid}'")) == "approved"
    assert publish(Client("thu.mgr@x.test"), world.day(THU)).status_code == 200


def test_repeating_a_decision_changes_nothing(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    rid, _ = submit(Client("thu.youth@x.test"), world)
    assert decide(world, rid, "approved").status_code == 200
    assert decide(world, rid, "rejected").status_code == 200  # already decided: ignored
    assert db.scalar(text(f"select status from registration_requests where id = '{rid}'")) == "approved"


def test_only_the_day_manager_or_admin_decides(world, clock):
    clock.set(at(CLOSED, "12:00"))
    rid, _ = submit(Client("thu.youth@x.test"), world)
    assert Client("sat.mgr@x.test").post(f"/api/manage/requests/{rid}/decision", {"decision": "approved"}).status_code == 404
    assert Client("thu.youth@x.test").post(f"/api/manage/requests/{rid}/decision", {"decision": "approved"}).status_code == 404
    assert Client("admin@x.test").post(f"/api/manage/requests/{rid}/decision", {"decision": "approved"}).status_code == 200


def test_no_request_can_join_after_publication_even_for_registered_non_assignees(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")  # registers, is not assigned
    from tests.helpers import register
    assert register(vol, world.day(THU), evening=True).status_code == 200
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    assert publish(mgr, world.day(THU)).status_code == 200
    clock.set(at(CLOSED, "12:00"))
    _, r = submit(vol, world)
    assert r.status_code == 409 and r.json()["code"] == "schedule_published"
    _, r = submit(Client("thu.trainee2@x.test"), world)
    assert r.status_code == 409 and r.json()["code"] == "schedule_published"


def test_a_request_racing_a_publication_cannot_both_succeed(world, db, clock):
    clock.set(at(CLOSED, "12:00"))
    mgr = Client("thu.mgr@x.test")
    shift(board(mgr, world.day(THU)), "evening")  # a draft exists before the race
    vols = [Client(e) for e in ("thu.youth@x.test", "thu.trainee@x.test", "thu.trainee2@x.test")]
    results = {}

    def go(name, fn):
        results[name] = fn()

    threads = [threading.Thread(target=go, args=("pub", lambda: publish(mgr, world.day(THU)).status_code))]
    threads += [threading.Thread(target=go, args=(f"sub{i}", (lambda c=c: submit(c, world)[1].status_code)))
                for i, c in enumerate(vols)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    subs = [v for k, v in results.items() if k.startswith("sub")]
    published = results["pub"] == 200
    accepted = [s for s in subs if s == 201]
    # Invariant: a publication and an accepted request never both succeed, and no accepted request is
    # left behind a published schedule.
    assert not (published and accepted), results
    if published:
        assert db.scalar(text(f"select count(*) from registration_requests where activity_day_id = '{world.day(THU)}' "
                              f"and status = 'pending'")) == 0
    assert set(subs) <= {201, 409}, results
