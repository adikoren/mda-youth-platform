"""Manager date list sections (decision 2026-10-17). Main: today through the next seven days, nearest first, until
the date's last shift ends. History: completed dates, most recent first. Asia/Jerusalem decides "today"."""
from datetime import datetime, timedelta, timezone

from tests.conftest import Client, at

ADMIN = "admin@x.test"  # manages every date


def dates(items, section):
    return [d["date"] for d in items if d["section"] == section]


def test_main_list_is_today_through_seven_days_and_history_is_completed_dates(world, clock):
    clock.set(at("2026-10-14", "12:00"))
    items = Client(ADMIN).get("/api/manage/days").json()
    main, hist = dates(items, "main"), dates(items, "history")
    assert main == ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19",
                    "2026-10-20", "2026-10-21"]  # today + 7 days, nearest first
    assert "2026-10-22" not in [d["date"] for d in items]  # beyond the window: not in the main list
    assert hist == ["2026-10-13", "2026-10-12", "2026-10-11"]  # completed, most recent first


def test_date_at_seven_days_is_in_and_eight_days_is_out(world, clock):
    clock.set(at("2026-10-14", "12:00"))
    dates_seen = {d["date"]: d["section"] for d in Client(ADMIN).get("/api/manage/days").json()}
    assert dates_seen["2026-10-21"] == "main"
    assert "2026-10-22" not in dates_seen


def test_today_stays_in_main_until_its_last_shift_ends_then_moves_to_history(world, clock):
    clock.set(at("2026-10-14", "23:00") - timedelta(seconds=1))
    seen = {d["date"]: d["section"] for d in Client(ADMIN).get("/api/manage/days").json()}
    assert seen["2026-10-14"] == "main"
    clock.set(at("2026-10-14", "23:00"))  # the evening shift ends at 23:00
    seen = {d["date"]: d["section"] for d in Client(ADMIN).get("/api/manage/days").json()}
    assert seen["2026-10-14"] == "history"
    assert dates([{"date": k, "section": v} for k, v in seen.items()], "main")[0] == "2026-10-15"


def test_today_is_decided_in_asia_jerusalem_not_utc(world, clock):
    # 22:30 UTC on 13 Oct is 01:30 on 14 Oct in Jerusalem (summer time): today is 14 Oct.
    clock.set(datetime(2026, 10, 13, 22, 30, tzinfo=timezone.utc))
    items = Client(ADMIN).get("/api/manage/days").json()
    assert dates(items, "main")[0] == "2026-10-14"
