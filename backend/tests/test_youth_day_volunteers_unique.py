"""Each user ID appears exactly once in the selected-day volunteer list, whatever roles or appointments the person
has. Roles that apply to the same person are shown on that person's single entry, not as a second row."""
from collections import Counter

from tests.conftest import Client, at
from tests.test_temporary_day_managers import temp

THU_WD = 4
YM = "youthmgr@x.test"


def listing(wd=THU_WD) -> dict:
    r = Client(YM).get(f"/api/youth/days/{wd}/volunteers")
    assert r.status_code == 200, r.text
    return r.json()


def all_ids(body) -> list[str]:
    return ([m["user_id"] for m in body["day_managers"]] + [t["user_id"] for t in body["temporary_managers"]]
            + [v["user_id"] for v in body["volunteers"]])


def assert_unique(body):
    dups = [uid for uid, n in Counter(all_ids(body)).items() if n > 1]
    assert dups == [], f"repeated user ids: {dups}"


def test_plain_day_has_each_volunteer_once(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    body = listing()
    assert_unique(body)
    assert str(world.uid("thu_mgr")) not in {v["user_id"] for v in body["volunteers"]}


def test_temporary_manager_who_is_a_home_volunteer_appears_once(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    # thu_mentor is a Thursday home volunteer (a mentor, the only level a manager may be); a temporary Thursday
    # appointment makes them a manager that day, so they must not also be listed as a volunteer.
    r = temp(Client(YM), world.uid("thu_mentor"), THU_WD, "2026-10-12T20:00", "2026-10-30T23:00")
    assert r.status_code == 201, r.text
    body = listing()
    assert_unique(body)
    temp_entry = next(t for t in body["temporary_managers"] if t["user_id"] == str(world.uid("thu_mentor")))
    assert temp_entry["home_member"] is True  # the single entry still says they are a home volunteer
    assert str(world.uid("thu_mentor")) not in {v["user_id"] for v in body["volunteers"]}


def test_permanent_manager_who_is_also_temporary_appears_once_with_both_roles(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    assert temp(Client(YM), world.uid("thu_mgr"), THU_WD, "2026-10-12T20:00", "2026-10-30T23:00").status_code == 201
    body = listing()
    assert_unique(body)
    pinned = [m for m in body["day_managers"] if m["user_id"] == str(world.uid("thu_mgr"))]
    assert len(pinned) == 1 and pinned[0]["temporary_until"] is not None  # both roles on the pinned entry
    assert str(world.uid("thu_mgr")) not in {t["user_id"] for t in body["temporary_managers"]}
    assert str(world.uid("thu_mgr")) not in {v["user_id"] for v in body["volunteers"]}
