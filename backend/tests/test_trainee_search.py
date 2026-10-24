"""Trainee list search: partial, trimmed, case-insensitive name match within the caller's own scope; one entry per
user ID; no bypass of the day-scoped access rules."""
from tests.conftest import Client, at


def names(email, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = Client(email).get(f"/api/journal/trainees?{qs}" if qs else "/api/journal/trainees")
    assert r.status_code == 200, r.text
    return r.json()


def test_partial_and_trimmed_search_within_the_day(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    full = names("youthmgr@x.test", weekday=4)
    target = next(t["full_name"] for t in full if t["id"] == str(world.uid("thu_trainee")))
    part = target[1:4]  # a fragment from the middle of the name
    hits = names("youthmgr@x.test", weekday=4, search=f"%20%20{part.replace(' ', '%20')}%20%20")
    assert str(world.uid("thu_trainee")) in {t["id"] for t in hits}
    assert all(part in t["full_name"] for t in hits)
    assert len(hits) == len({t["id"] for t in hits})  # each trainee once


def test_no_match_returns_empty_list_not_an_error(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    assert names("youthmgr@x.test", weekday=4, search="zzzz-no-such-name") == []


def test_search_cannot_widen_a_day_managers_scope(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    mon_name = next(t["full_name"] for t in names("youthmgr@x.test", weekday=1)
                    if t["id"] == str(world.uid("mon_trainee")))
    ids = {t["id"] for t in names("thu.mgr@x.test", search=mon_name)}
    assert str(world.uid("mon_trainee")) not in ids  # another day's trainee stays invisible
    assert Client("thu.mgr@x.test").get("/api/journal/trainees?weekday=1&search=a").status_code == 404
