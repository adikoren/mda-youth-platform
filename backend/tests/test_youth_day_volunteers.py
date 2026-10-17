"""Youth Manager's selected-day volunteer list (decision of 2026-10-17): the day's volunteers by home day, the day
manager pinned and never invented, and no access for Day Managers or ordinary volunteers."""
from tests.conftest import Client, at

THU_WD = 4


def get(email, wd=THU_WD):
    return Client(email).get(f"/api/youth/days/{wd}/volunteers")


def test_youth_manager_and_admin_see_the_day_with_its_manager_pinned(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    for email in ("youthmgr@x.test", "admin@x.test"):
        r = get(email)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["weekday_name"] == "חמישי"
        assert [m["user_id"] for m in body["day_managers"]] == [str(world.uid("thu_mgr"))]
        ids = {v["user_id"] for v in body["volunteers"]}
        assert {str(world.uid("thu_youth")), str(world.uid("thu_mentor")), str(world.uid("thu_trainee"))} <= ids
        assert str(world.uid("thu_mgr")) not in ids  # the pinned manager is not repeated in the list
        assert str(world.uid("mon_vol")) not in ids  # other home days are not included


def test_a_day_without_a_manager_reports_none(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    body = get("youthmgr@x.test", wd=2).json()  # Tuesday has no manager in the fixture
    assert body["day_managers"] == []


def test_only_youth_manager_and_admin_may_read_it(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    assert get("thu.mgr@x.test").status_code == 404  # a Day Manager does not get it
    assert get("thu.youth@x.test").status_code == 404  # ordinary volunteer
    assert get("youthmgr@x.test", wd=9).status_code == 404  # not a weekday
