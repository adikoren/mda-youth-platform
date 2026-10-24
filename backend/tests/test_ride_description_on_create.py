"""A ride's case description, sent when the ride is created, is kept for those who may write it (the trainee and
Admin) and refused, not silently dropped, for everyone else. Regression: Admin's description was discarded."""
from sqlalchemy import text

from tests.test_journal import evening, new_ride  # noqa: F401  (fixture and helper)
from tests.conftest import Client, at
from tests.test_journal import THU


def test_admin_description_on_create_is_stored(world, db, clock, evening):
    rid, r = new_ride(Client("admin@x.test"), world, trainee="thu_trainee", shift_id=evening, description="כאב בחזה")
    assert r.status_code == 201, r.text
    assert db.scalar(text(f"select description from rides where id = '{rid}'")) == "כאב בחזה"


def test_trainee_description_on_create_is_stored(world, db, clock, evening):
    clock.set(at(THU, "17:00"))
    rid, r = new_ride(Client("thu.trainee@x.test"), world, trainee="thu_trainee", shift_id=evening,
                      description="סחרחורת")
    assert r.status_code == 201, r.text
    assert db.scalar(text(f"select description from rides where id = '{rid}'")) == "סחרחורת"


def test_mentor_description_on_create_is_ignored_as_before(world, db, clock, evening):
    clock.set(at(THU, "17:00"))
    rid, r = new_ride(Client("thu.mentor@x.test"), world, trainee="thu_trainee", shift_id=evening,
                      description="ignored for mentors")
    assert r.status_code == 201 and r.json()["description"] is None
