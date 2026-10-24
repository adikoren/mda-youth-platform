"""The demo publication audit flags a published date whose registration window is still open, and nothing else."""
from app.demo import dataset
from tests.conftest import Client, at
from tests.helpers import board, publish

THU = "2026-10-15"


def test_audit_flags_a_schedule_published_while_registration_is_open(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))  # Thursday registration is open
    board(Client("thu.mgr@x.test"), world.day(THU))  # creates the draft that publication takes
    assert publish(Client("thu.mgr@x.test"), world.day(THU)).status_code == 200
    problems = dataset.publication_audit(db, clock.now())
    assert [p["date"] for p in problems] == [THU]


def test_audit_is_empty_after_registration_closed_before_publication(world, db, clock):
    clock.set(at("2026-10-14", "12:00"))  # closed on 13.10 at 18:00
    board(Client("thu.mgr@x.test"), world.day(THU))  # creates the draft that publication takes
    assert publish(Client("thu.mgr@x.test"), world.day(THU)).status_code == 200
    assert dataset.publication_audit(db, clock.now()) == []
