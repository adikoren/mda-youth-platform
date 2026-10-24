"""Schedule list classification and visibility (decision 2026-10-16). A date is historical automatically once
every shift of that date has ended (server time). History follows the same visibility rules as active schedules."""
from datetime import date, timedelta

from sqlalchemy import func, select

from app.days.models import ActivityDay, Shift
from tests.conftest import Client, at
from tests.test_journal import THU, publish_evening


def listed(client: Client, start: str, end: str) -> dict:
    r = client.get(f"/api/schedule?start={start}&end={end}")
    assert r.status_code == 200, r.text
    return {i["date"]: i for i in r.json()["items"]}


def test_date_is_active_until_its_last_shift_ends_then_historical(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU)
    vol = Client("thu.youth@x.test")
    ends = db.scalar(select(func.max(Shift.ends_at)).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                     .where(ActivityDay.local_date == date.fromisoformat(THU)))
    clock.set(at("2026-10-15", "18:00"))
    assert listed(vol, THU, THU)[THU]["historical"] is False
    clock.set(ends - timedelta(seconds=1))
    assert listed(vol, THU, THU)[THU]["historical"] is False
    clock.set(ends)
    assert listed(vol, THU, THU)[THU]["historical"] is True


def test_history_keeps_the_same_visibility_rules(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    publish_evening(world, THU)
    clock.set(at("2026-10-20", "12:00"))  # long after the date
    # A volunteer from another weekday with no assignment must not see the Thursday history.
    assert THU not in listed(Client("mon.vol@x.test"), THU, THU)
    # Assigned and home-day volunteers still see it, now as history.
    assert listed(Client("thu.youth@x.test"), THU, THU)[THU]["historical"] is True
