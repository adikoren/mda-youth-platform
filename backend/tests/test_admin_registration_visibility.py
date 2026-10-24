"""Admin sees every date with open registration, whatever the home day, invitations or level (decision of
2026-10-17). Listing never registers anyone; answering keeps its existing rules (a volunteer level is required)."""
from datetime import date

from sqlalchemy import select, update

from app.accounts.models import VolunteerLevel
from app.days.models import ActivityDay
from tests.conftest import Client, at, make_user

SUN, MON = "2026-10-18", "2026-10-19"  # both open for registration on 15 Oct


def listed(client):
    r = client.get("/api/registrations")
    assert r.status_code == 200, r.text
    return {d["date"] for d in r.json()}


def test_admin_sees_open_dates_on_all_weekdays_regardless_of_home_day(world, clock):
    clock.set(at("2026-10-15", "12:00"))
    assert {SUN, MON} <= listed(Client("admin@x.test"))


def test_viewing_does_not_register_the_admin(world, db, clock):
    clock.set(at("2026-10-15", "12:00"))
    admin = Client("admin@x.test")
    listed(admin)
    day = db.scalar(select(ActivityDay).where(ActivityDay.local_date == date(2026, 10, 18)))
    shifts = admin.get(f"/api/registrations/{day.id}").json()["shifts"]
    assert shifts and all(s["available"] is None for s in shifts)


def test_admin_without_a_volunteer_level_still_sees_the_list(world, db, clock):
    clock.set(at("2026-10-15", "12:00"))
    user = make_user(db, "admin-nolevel@x.test", "mentor", None, clock.now(), roles=("admin",))
    db.execute(update(VolunteerLevel).where(VolunteerLevel.user_id == user.id).values(valid_to=clock.now()))
    db.commit()
    assert {SUN, MON} <= listed(Client("admin-nolevel@x.test"))


def test_other_roles_keep_their_restrictions(world, clock):
    clock.set(at("2026-10-15", "12:00"))
    dates = listed(Client("thu.youth@x.test"))  # Thursday volunteer: home day and Saturday only
    assert SUN not in dates and MON not in dates
