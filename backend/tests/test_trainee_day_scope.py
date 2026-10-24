"""Trainee progress list by day (decision of 2026-10-17): youth managers and admin may filter by weekday; a Day
Manager sees only their own day's trainees; Saturday managers have no trainee-management access."""
from tests.conftest import Client, at


def test_youth_manager_filters_trainees_by_day(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    all_ids = {t["id"] for t in Client("youthmgr@x.test").get("/api/journal/trainees").json()}
    thu = Client("youthmgr@x.test").get("/api/journal/trainees?weekday=4").json()
    assert {t["id"] for t in thu} <= all_ids
    assert all(t["home_weekday"] == 4 for t in thu)
    assert str(world.uid("thu_trainee")) in {t["id"] for t in thu}
    assert str(world.uid("mon_trainee")) not in {t["id"] for t in thu}


def test_day_manager_sees_only_their_own_day(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    ids = {t["id"] for t in Client("thu.mgr@x.test").get("/api/journal/trainees").json()}
    assert str(world.uid("thu_trainee")) in ids
    assert str(world.uid("mon_trainee")) not in ids
    assert Client("thu.mgr@x.test").get("/api/journal/trainees?weekday=1").status_code == 404  # not their day


def test_saturday_manager_has_no_trainee_access(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    assert Client("sat.mgr@x.test").get("/api/journal/trainees").status_code == 403
    assert Client("sat.mgr@x.test").get("/api/journal/trainees?weekday=6").status_code == 403
