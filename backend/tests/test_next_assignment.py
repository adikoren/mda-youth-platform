"""The home card shows only the nearest live published assignment (decision 2026-10-16). An active shift counts
until it ends; after it ends, or after a volunteer cancels, the card falls back to the next one or to nothing."""
from datetime import date, timedelta

from sqlalchemy import func, select

from app.days.models import ActivityDay, Shift
from tests.conftest import Client, at
from tests.test_journal import THU, publish_evening


def evening_times(db):
    return db.execute(select(Shift.starts_at, Shift.ends_at).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                      .where(ActivityDay.local_date == date.fromisoformat(THU), Shift.kind == "evening")).one()


def next_card(client: Client):
    r = client.get("/api/me/next-assignment")
    assert r.status_code == 200, r.text
    return r.json()["assignment"]


def test_active_shift_is_the_card_and_cannot_be_cancelled_once_started(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU)
    starts, ends = evening_times(db)
    vol = Client("thu.youth@x.test")

    clock.set(starts - timedelta(hours=1))
    card = next_card(vol)
    assert card["date"] == THU and card["kind"] == "evening" and card["state"] == "before"
    assert card["cancellable"] is True and card["ambulance"] == 2  # thu_youth sits in the second ambulance slot

    clock.set(starts + timedelta(hours=1))
    card = next_card(vol)
    assert card is not None and card["state"] == "started" and card["cancellable"] is False


def test_card_is_empty_after_the_only_shift_ends(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU)
    _, ends = evening_times(db)
    clock.set(ends)
    assert next_card(Client("thu.youth@x.test")) is None


def test_unassigned_volunteer_has_no_card(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU)
    assert next_card(Client("mon.vol@x.test")) is None
