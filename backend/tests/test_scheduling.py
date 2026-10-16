import threading

from sqlalchemy import select, text

from app.notifications.models import OutboxMessage
from tests.conftest import Client, at
from tests.helpers import assign, board, publish, register, shift

THU = "2026-10-15"
SAT = "2026-10-17"
FRI = "2026-10-16"


def crew_ids(client: Client, day_id: str) -> set[str]:
    r = client.get(f"/api/schedule/{day_id}")
    assert r.status_code == 200, r.text
    return {p["user_id"] for s in r.json()["shifts"] for sl in s["slots"] for p in sl["crew"]}


def setup_thursday(world, extra_registrants=("thu_trainee", "thu_youth", "thu_mentor")):
    for key in extra_registrants:
        assert register(Client(f"{world.users[key].email}"), world.day(THU), evening=True).status_code == 200
    mgr = Client("thu.mgr@x.test")
    return mgr, board(mgr, world.day(THU))


# ---------- draft isolation and manual publication ----------

def test_draft_hidden_until_manual_publish_and_target_time_never_publishes(world, clock):
    mgr, b = setup_thursday(world)
    ev = shift(b, "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    member = Client("thu.youth@x.test")
    for c in (member, Client("youthmgr@x.test"), Client("thu.trainee@x.test")):
        assert c.get(f"/api/schedule/{world.day(THU)}").status_code == 404
    clock.set(at("2026-10-14", "20:30"))  # past the publication target
    assert member.get(f"/api/schedule/{world.day(THU)}").status_code == 404
    assert publish(mgr, world.day(THU)).status_code == 200
    assert crew_ids(member, world.day(THU)) == {world.uid("thu_trainee")}


def test_publish_retry_is_idempotent_and_stale_draft_conflicts(world, db):
    mgr, b = setup_thursday(world)
    ev = shift(b, "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    cur = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()["draft"]
    stale = mgr.post(f"/api/manage/days/{world.day(THU)}/publish",
                     {"draft_id": cur["id"], "expected_edit_count": cur["edit_count"] - 1})
    assert stale.status_code == 409 and stale.json()["code"] == "stale_draft"
    body = {"draft_id": cur["id"], "expected_edit_count": cur["edit_count"]}
    first = mgr.post(f"/api/manage/days/{world.day(THU)}/publish", body)
    again = mgr.post(f"/api/manage/days/{world.day(THU)}/publish", body)
    assert first.json()["already_published"] is False and again.json()["already_published"] is True
    n = db.query(OutboxMessage).count()
    assert n == first.json()["notifications_queued"] and n > 0
    assert db.scalar(text("select count(*) from audit_events where action='publish'")) == 1


def test_post_publication_draft_edits_have_no_live_effect_until_republished(world, db):
    mgr, b = setup_thursday(world)
    ev = shift(b, "evening")
    slot = ev["slots"][0]["slot_key"]
    assign(mgr, slot, world.uid("thu_trainee"))
    publish(mgr, world.day(THU))
    before = db.query(OutboxMessage).count()

    board(mgr, world.day(THU))  # new working draft copied from the publication
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/unassign", {"user_id": world.uid("thu_trainee")}).status_code == 200
    assert mgr.post(f"/api/manage/slots/{shift(board(mgr, world.day(THU)), 'evening')['slots'][1]['slot_key']}/cancel").status_code == 200
    trainee = Client("thu.trainee@x.test")
    assert crew_ids(trainee, world.day(THU)) == {world.uid("thu_trainee")}  # live view unchanged
    assert trainee.get("/api/me/history").json()[0]["date"] == THU
    assert db.query(OutboxMessage).count() == before  # no change notifications from a draft

    b2 = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()
    assert b2["draft"]["dirty"] is True
    r = publish(mgr, world.day(THU))
    assert r.status_code == 200 and r.json()["already_published"] is False
    assert crew_ids(trainee, world.day(THU)) == set()
    assert trainee.get("/api/me/history").json() == []
    unassigned = db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "unassigned")).all()
    assert [m.recipient_user_id for m in unassigned] == [world.users["thu_trainee"].id]
    # The removal lists the shift it came from. Schedule references are added only because the trainee is
    # still waiting for another shift of the same date (decision 2026-10-16); a person with no remaining role
    # on the date gets date and shift kinds only.
    assert set(unassigned[0].payload) <= {"date", "shifts", "activity_day_id", "revision"}
    assert {"shift": "evening", "change": "unassigned"} in unassigned[0].payload["shifts"]
    if "activity_day_id" not in unassigned[0].payload:
        assert set(unassigned[0].payload) == {"date", "shifts"}


def test_republication_creates_new_legitimate_notifications(world, db):
    mgr, b = setup_thursday(world)
    ev = shift(b, "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    publish(mgr, world.day(THU))
    first_assigned = db.query(OutboxMessage).filter_by(event_type="assigned").count()
    board(mgr, world.day(THU))
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_youth"))
    publish(mgr, world.day(THU))
    assigned = db.query(OutboxMessage).filter_by(event_type="assigned").count()
    changed = db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "schedule_changed")).all()
    assert assigned == first_assigned + 1
    assert [m.recipient_user_id for m in changed] == [world.users["thu_trainee"].id]
    # The still-waiting evening registrant hears about the change too (decision 2026-10-12).
    waiting = db.scalars(select(OutboxMessage).where(OutboxMessage.event_type == "schedule_changed_waiting")).all()
    assert [m.recipient_user_id for m in waiting] == [world.users["thu_mentor"].id]


# ---------- hard blocks and soft warnings ----------

def test_capacity_excess_warns_but_allows_assignment_and_publication(world):
    mgr, b = setup_thursday(world)
    slot = shift(b, "evening")["slots"][0]["slot_key"]
    assert mgr.patch(f"/api/manage/slots/{slot}", {"capacity": 2}).status_code == 200
    for key in ("thu_trainee", "thu_youth", "thu_mentor"):
        assert assign(mgr, slot, world.uid(key)).status_code == 201
    b = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()
    assert shift(b, "evening")["slots"][0]["warning"] == "הוגדרו 2 מקומות, אך משובצים 3 מתנדבים"
    assert publish(mgr, world.day(THU)).status_code == 200


def test_same_person_cannot_hold_two_slots_in_one_shift_but_can_move(world):
    mgr, b = setup_thursday(world)
    s0, s1 = [s["slot_key"] for s in shift(b, "evening")["slots"][:2]]
    assert assign(mgr, s0, world.uid("thu_youth")).status_code == 201
    r = assign(mgr, s1, world.uid("thu_youth"))
    assert r.status_code == 409 and r.json()["code"] == "already_in_shift"
    ev_id = shift(b, "evening")["id"]
    assert mgr.post(f"/api/manage/shifts/{ev_id}/move", {"user_id": world.uid("thu_youth"), "to_slot_key": s1}).status_code == 200
    b = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()
    crews = [[p["user_id"] for p in sl["crew"]] for sl in shift(b, "evening")["slots"][:2]]
    assert crews == [[], [world.uid("thu_youth")]]


def test_concurrent_assignment_of_same_person_to_two_slots(world, engine):
    mgr, b = setup_thursday(world)
    keys = [s["slot_key"] for s in shift(b, "evening")["slots"][:2]]
    clients = [Client("thu.mgr@x.test"), Client("thu.mgr@x.test")]
    results = []
    barrier = threading.Barrier(2)

    def go(i):
        barrier.wait()
        results.append(assign(clients[i], keys[i], world.uid("thu_mentor")).status_code)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [201, 409]
    with engine.connect() as conn:
        n = conn.execute(text("select count(*) from assignments a join schedule_versions v on v.id=a.version_id "
                              "where v.status='draft'")).scalar()
    assert n == 1


def test_distinct_users_may_concurrently_overfill_a_slot(world):
    mgr, b = setup_thursday(world)
    key = shift(b, "evening")["slots"][0]["slot_key"]
    mgr_api = mgr.patch(f"/api/manage/slots/{key}", {"capacity": 1})
    assert mgr_api.status_code == 200
    results = []
    barrier = threading.Barrier(2)

    def go(user_key):
        c = Client("thu.mgr@x.test")
        barrier.wait()
        results.append(assign(c, key, world.uid(user_key)).status_code)

    threads = [threading.Thread(target=go, args=(k,)) for k in ("thu_youth", "thu_mentor")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [201, 201]  # capacity is a warning, not a lock-out


def test_trainee_cannot_be_in_reinforcement_and_evening(world):
    mgr, b = setup_thursday(world)
    reinf, ev = shift(b, "reinforcement"), shift(b, "evening")
    # Evening-available trainee is a reinforcement candidate, derived from availability.
    assert world.uid("thu_trainee") in [p["user_id"] for p in reinf["waiting"]]
    assert assign(mgr, reinf["slots"][0]["slot_key"], world.uid("thu_trainee")).status_code == 201
    r = assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    assert r.status_code == 409 and r.json()["code"] == "trainee_reinforcement_evening"
    # Non-trainees are not restricted by this rule.
    assert assign(mgr, reinf["slots"][0]["slot_key"], world.uid("thu_youth")).status_code == 201
    assert assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_youth")).status_code == 201


def test_published_rows_are_immutable_in_the_database(world, engine):
    mgr, b = setup_thursday(world)
    assign(mgr, shift(b, "evening")["slots"][0]["slot_key"], world.uid("thu_trainee"))
    publish(mgr, world.day(THU))
    with engine.connect() as conn:
        try:
            conn.execute(text("update ambulance_slots s set capacity = 7 from schedule_versions v "
                              "where v.id = s.version_id and v.status = 'published'"))
            raised = False
        except Exception as exc:  # noqa: BLE001
            raised = "immutable" in str(exc)
        conn.rollback()
    assert raised


# ---------- waiting lists, order, manager-added participants ----------

def test_assigning_evening_keeps_morning_availability_and_waiting(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    c = Client("mon.vol@x.test")
    register(c, world.day(SAT), morning=True, evening=True)
    mgr = Client("sat.mgr@x.test")
    b = board(mgr, world.day(SAT))
    assign(mgr, shift(b, "evening")["slots"][0]["slot_key"], world.uid("mon_vol"))
    b = mgr.get(f"/api/manage/days/{world.day(SAT)}/board").json()
    morning_waiting = [p["user_id"] for p in shift(b, "morning")["waiting"]]
    assert morning_waiting == [world.uid("mon_vol")]
    assert shift(b, "evening")["waiting"] == []


def test_manager_added_returns_to_waiting_after_published_cancellation_with_rank(world):
    mgr, b = setup_thursday(world, extra_registrants=("thu_trainee",))
    ev = shift(b, "evening")
    # Add a non-registrant explicitly; provenance is "manager".
    assert mgr.post(f"/api/manage/shifts/{ev['id']}/participants", {"user_id": world.uid("thu_mentor")}).status_code == 201
    order = [world.uid("thu_mentor"), world.uid("thu_trainee")]
    assert mgr.put(f"/api/manage/shifts/{ev['id']}/order", {"user_ids": order}).status_code == 200
    slot = ev["slots"][0]["slot_key"]
    assign(mgr, slot, world.uid("thu_mentor"))
    publish(mgr, world.day(THU))
    board(mgr, world.day(THU))
    mgr.post(f"/api/manage/slots/{slot}/cancel")
    publish(mgr, world.day(THU))
    b = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()
    waiting = shift(b, "evening")["waiting"]
    assert [p["user_id"] for p in waiting] == order  # manual order survived cancel + republish
    assert waiting[0]["source"] == "manager"


def test_direct_guest_addition_creates_confirmation_request(world):
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    r = assign(mgr, shift(b, "evening")["slots"][0]["slot_key"], world.uid("mon_vol"))
    assert r.status_code == 201  # confirmation preferred, not required
    guest = Client("mon.vol@x.test")
    cards = guest.get("/api/invitations").json()
    assert [c["kind"] for c in cards] == ["direct_add"]
    assert guest.post(f"/api/invitations/{cards[0]['id']}/confirm").status_code == 200
    assert guest.get("/api/invitations").json() == []
    b = mgr.get(f"/api/manage/days/{world.day(THU)}/board").json()
    person = shift(b, "evening")["slots"][0]["crew"][0]
    assert person["is_guest"] and person["confirmation"] == "confirmed"


# ---------- published access windows ----------

def test_assignee_outside_home_day_keeps_the_date_schedule_after_the_shift(world, clock):
    """Decision 2026-10-16: a recorded assignment to a date grants that date's schedule with no time window
    (it stays in history). Other dates on the same weekday are not granted."""
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    ev = shift(b, "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("mon_vol"))
    register(Client("thu.youth@x.test"), world.day(THU), evening=True)
    assign(mgr, ev["slots"][1]["slot_key"], world.uid("thu_youth"))
    guest = Client("mon.vol@x.test")
    assert guest.get(f"/api/schedule/{world.day(THU)}").status_code == 404  # not before publication
    publish(mgr, world.day(THU))
    assert crew_ids(guest, world.day(THU)) == {world.uid("mon_vol"), world.uid("thu_youth")}  # full schedule
    assert guest.get(f"/api/schedule/{world.day(THU)}").json()["assigned_outside_home"] is True
    clock.set(at(THU, "23:30"))  # after the shift end
    assert guest.get(f"/api/schedule/{world.day(THU)}").status_code == 200
    clock.set(at("2026-10-16", "00:00"))  # after midnight: the old time window used to end access here
    assert guest.get(f"/api/schedule/{world.day(THU)}").status_code == 200
    assert [d for d in guest.get("/api/schedule").json()["items"] if d["date"] == THU][0]["historical"] is True
    # Personal history remains.
    assert [h["date"] for h in guest.get("/api/me/history").json()] == [THU]
    # Home-day member keeps permanent access.
    clock.set(at("2027-01-01", "10:00"))
    assert Client("thu.youth@x.test").get(f"/api/schedule/{world.day(THU)}").status_code == 200


def test_guest_loses_access_on_published_cancellation_not_on_draft(world):
    mgr = Client("thu.mgr@x.test")
    b = board(mgr, world.day(THU))
    slot = shift(b, "evening")["slots"][0]["slot_key"]
    assign(mgr, slot, world.uid("mon_vol"))
    publish(mgr, world.day(THU))
    guest = Client("mon.vol@x.test")
    board(mgr, world.day(THU))
    mgr.post(f"/api/manage/slots/{slot}/cancel")
    assert guest.get(f"/api/schedule/{world.day(THU)}").status_code == 200  # draft has no live effect
    publish(mgr, world.day(THU))
    assert guest.get(f"/api/schedule/{world.day(THU)}").status_code == 404


def test_saturday_published_schedule_visible_to_all_active_volunteers(world, clock):
    mgr = Client("sat.mgr@x.test")
    board(mgr, world.day(SAT))
    publish(mgr, world.day(SAT))
    for email in ("mon.trainee@x.test", "thu.youth@x.test"):
        assert Client(email).get(f"/api/schedule/{world.day(SAT)}").status_code == 200
    clock.set(at("2027-02-01", "10:00"))
    assert Client("mon.trainee@x.test").get(f"/api/schedule/{world.day(SAT)}").status_code == 200
    # Friday is NOT open to everyone.
    fmgr = Client("admin@x.test")
    board(fmgr, world.day(FRI))
    publish(fmgr, world.day(FRI))
    assert Client("mon.trainee@x.test").get(f"/api/schedule/{world.day(FRI)}").status_code == 404


def test_youth_manager_reads_published_only_and_cannot_edit(world):
    mgr, b = setup_thursday(world)
    ym = Client("youthmgr@x.test")
    assert ym.get(f"/api/manage/days/{world.day(THU)}/board").status_code == 403
    assert ym.post(f"/api/manage/days/{world.day(THU)}/draft").status_code == 403
    assert ym.post(f"/api/manage/slots/{shift(b, 'evening')['slots'][0]['slot_key']}/assign",
                   {"user_id": world.uid("thu_youth")}).status_code == 403
    assert ym.get(f"/api/schedule/{world.day(THU)}").status_code == 404
    publish(mgr, world.day(THU))
    assert ym.get(f"/api/schedule/{world.day(THU)}").status_code == 200


def test_list_and_count_endpoints_respect_visibility(world, clock):
    thu_mgr = Client("thu.mgr@x.test")
    board(thu_mgr, world.day(THU))
    publish(thu_mgr, world.day(THU))
    admin = Client("admin@x.test")
    board(admin, world.day("2026-10-19"))  # a Monday
    publish(admin, world.day("2026-10-19"))
    mon = Client("mon.vol@x.test").get("/api/schedule?start=2026-10-11&end=2026-10-31").json()
    assert [d["date"] for d in mon["items"]] == ["2026-10-19"] and mon["total_visible"] == 1
    thu = Client("thu.youth@x.test").get("/api/schedule?start=2026-10-11&end=2026-10-31").json()
    assert [d["date"] for d in thu["items"]] == [THU] and thu["total_visible"] == 1
    assert Client("youthmgr@x.test").get("/api/schedule").json()["total_visible"] == 2
    # Managed-day list only contains scoped weekdays.
    days = Client("thu.mgr@x.test").get("/api/manage/days?start=2026-10-11&end=2026-10-31").json()
    assert {d["weekday"] for d in days} == {4}
    assert Client("thu.youth@x.test").get("/api/manage/days").json() == []
    # Volunteers cannot search people.
    assert Client("thu.youth@x.test").get("/api/users/search?q=thu").status_code == 403


# ---------- capacity setting limit (decision 2026-10-12) ----------

def test_capacity_setting_is_limited_to_3_but_crew_may_exceed_it(world):
    mgr, b = setup_thursday(world)
    ev = shift(b, "evening")
    slot = ev["slots"][0]["slot_key"]
    for bad in (4, 8, -1):
        r = mgr.patch(f"/api/manage/slots/{slot}", {"capacity": bad})
        assert r.status_code == 400 and r.json()["code"] == "capacity_out_of_range"
        r = mgr.post(f"/api/manage/shifts/{ev['id']}/slots", {"capacity": bad})
        assert r.status_code == 400
    assert mgr.patch(f"/api/manage/slots/{slot}", {"capacity": 3}).status_code == 200
    assert mgr.patch(f"/api/manage/slots/{slot}", {"capacity": 1}).status_code == 200
    # The limit is on the setting only: a second person is still assigned, with a warning.
    for key in ("thu_trainee", "thu_youth"):
        assert assign(mgr, slot, world.uid(key)).status_code == 201
    s1 = shift(mgr.get(f"/api/manage/days/{world.day(THU)}/board").json(), "evening")["slots"][0]
    assert s1["warning"] and s1["capacity_over_limit"] is False
    assert publish(mgr, world.day(THU)).status_code == 200


def test_legacy_capacity_above_limit_is_kept_flagged_and_reported(world, engine, db):
    from app.scheduling.service import slots_over_capacity_limit
    mgr, b = setup_thursday(world)
    slot = shift(b, "evening")["slots"][0]["slot_key"]
    with engine.begin() as conn:  # a value saved before the limit existed
        conn.execute(text("update ambulance_slots set capacity = 5 where slot_key = :k"), {"k": slot})
    publish(mgr, world.day(THU))
    board(mgr, world.day(THU))  # the new draft copies the published slot unchanged
    s1 = shift(mgr.get(f"/api/manage/days/{world.day(THU)}/board").json(), "evening")["slots"][0]
    assert s1["capacity"] == 5 and s1["capacity_over_limit"] is True
    report = slots_over_capacity_limit(db)
    assert {(r["capacity"], r["version_status"]) for r in report} == {(5, "published"), (5, "draft")}
    # A manager can lower it to a valid value; raising or keeping above 3 through the API is refused.
    assert mgr.patch(f"/api/manage/slots/{slot}", {"capacity": 5}).status_code == 400
    assert mgr.patch(f"/api/manage/slots/{slot}", {"capacity": 3}).status_code == 200
