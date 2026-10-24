"""Shift lifecycle in the volunteer UI: publication status decides where a date appears.
Unpublished -> the date is an entry under registration ('הרשמה למשמרות').
Published -> the entry leaves registration and the schedule ('סידור') shows it, under the same
visibility rules as before. Only publication status changes this; closing and shift start do not."""
from tests.conftest import Client, at
from tests.helpers import board, publish, assign, shift

THU, MON = "2026-10-15", "2026-10-12"


def registration_dates(client):
    return {r["date"] for r in client.get("/api/registrations").json()}


def schedule_dates(client):
    return {x["date"] for x in client.get("/api/schedule").json()["items"]}


def test_publication_moves_the_date_from_registration_to_schedule(world, clock):
    clock.set(at("2026-10-12", "21:00"))  # registration open
    vol = Client("thu.youth@x.test")  # eligible: Thursday is the home day
    assert THU in registration_dates(vol)
    assert THU not in schedule_dates(vol)  # not published yet
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    assert publish(mgr, world.day(THU)).status_code == 200
    # Published while registration was still open: the transition is immediate, not time-based. The date stays
    # on the home list as 'הסידור פורסם' (schedule link only) and offers no registration action.
    row = next(r for r in vol.get("/api/registrations").json() if r["date"] == THU)
    assert row["published"] is True and row["schedule_visible"] is True
    assert all(s["direct"] is False and s["request"] is False for s in row["shifts"])
    assert THU in schedule_dates(vol)
    admin_row = next(r for r in Client("admin@x.test").get("/api/registrations").json() if r["date"] == THU)
    assert admin_row["published"] is True and all(s["direct"] is False for s in admin_row["shifts"])
    assert Client("admin@x.test").get(f"/api/manage/days/{world.day(THU)}/board").status_code == 200


def test_unpublished_closed_entries_stay_under_registration(world, clock):
    clock.set(at("2026-10-14", "12:00"))  # Thursday registration closed on 13.10, not yet published
    vol = Client("thu.youth@x.test")
    rows = {r["date"]: r for r in vol.get("/api/registrations").json()}
    assert rows[THU]["window"] == "closed_late_allowed"
    assert THU not in schedule_dates(vol)


def test_schedule_visibility_is_unchanged_by_the_transition(world, clock):
    clock.set(at("2026-10-14", "12:00"))
    mgr = Client("thu.mgr@x.test")
    ev = shift(board(mgr, world.day(THU)), "evening")
    assign(mgr, ev["slots"][0]["slot_key"], world.uid("thu_trainee"))
    publish(mgr, world.day(THU))
    # Home-day member: permanent access, as before.
    assert THU in schedule_dates(Client("thu.youth@x.test"))
    # Assigned trainee: sees it.
    assert THU in schedule_dates(Client("thu.trainee@x.test"))
    # A volunteer with no home day here and no assignment: did not see it before, does not see it now.
    mon = Client("mon.vol@x.test")
    assert THU not in schedule_dates(mon)
    assert mon.get(f"/api/schedule/{world.day(THU)}").status_code == 404
