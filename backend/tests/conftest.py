"""Tests run against a real PostgreSQL database (MDA_TEST_DATABASE_URL), migrated with Alembic.
Concurrency, constraints, triggers and time-zone SQL are exercised for real, never mocked."""
import os
from dataclasses import dataclass
from datetime import date, datetime, time

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.access.clock import FixedClock, get_clock, set_clock
from app.accounts import service as accounts
from app.accounts.models import User
from app.accounts.security import hash_password
from app.core.config import get_settings
from app.core.db import Base, session_factory, set_engine
from app.core.timeutil import local_dt
from app.days import service as days
from app.main import app as fastapi_app
from app import models as _models  # noqa: F401

TEST_URL = get_settings().test_database_url
PASSWORD = "test-password-123"
_PW_HASH = None
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def at(d: str, hhmm: str) -> datetime:
    """Local Asia/Jerusalem wall clock -> UTC instant."""
    h, m = map(int, hhmm.split(":"))
    return local_dt(date.fromisoformat(d), time(h, m))


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_URL, future=True)
    # The test run wipes this database. Refuse anything not clearly named as a test database.
    assert (eng.url.database or "").endswith("_test"), f"refusing to wipe non-test database {eng.url.database!r}"
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    set_engine(eng)
    return eng


@pytest.fixture(autouse=True)
def clean(engine):
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def clock():
    c = FixedClock(at("2026-10-12", "12:00"))
    set_clock(c)
    fastapi_app.dependency_overrides[get_clock] = lambda: c
    yield c
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def db(engine):
    s = session_factory()()
    yield s
    s.rollback()
    s.close()


class Client:
    """A logged-in persona with its own cookie jar and CSRF header."""

    def __init__(self, email: str | None = None):
        self.http = TestClient(fastapi_app)
        token = self.http.get("/api/auth/csrf").json()["csrf"]
        self.headers = {"X-CSRF-Token": token}
        if email:
            r = self.post("/api/auth/login", {"email": email, "password": PASSWORD})
            assert r.status_code == 200, r.text

    def get(self, url, **kw):
        return self.http.get(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def post(self, url, json=None, **kw):
        return self.http.post(url, json=json, headers=self.headers, **kw)

    def put(self, url, json=None):
        return self.http.put(url, json=json, headers=self.headers)

    def patch(self, url, json=None):
        return self.http.patch(url, json=json, headers=self.headers)

    def delete(self, url):
        return self.http.delete(url, headers=self.headers)


@dataclass
class World:
    users: dict
    days: dict

    def uid(self, key):
        return str(self.users[key].id)

    def day(self, d: str):
        return str(self.days[d].id)


def make_user(db, email, level, home, now, roles=(), status="active"):
    global _PW_HASH
    _PW_HASH = _PW_HASH or hash_password(PASSWORD)
    u = User(email=email, full_name=email.split("@")[0], password_hash=_PW_HASH, status=status, created_at=now)
    db.add(u)
    db.flush()
    if status == "active":
        accounts.set_level(db, u.id, level, None, now)
        accounts.set_home_weekday(db, u.id, home, None, now)
    for r in roles:
        accounts.set_authority_role(db, u.id, r, True, None, now)
    return u


@pytest.fixture
def world(db, clock):
    now = clock.now()
    days.get_station(db)
    days.ensure_templates(db, now)
    U = {}
    U["admin"] = make_user(db, "admin@x.test", "mentor", None, now, roles=("admin",))
    U["youthmgr"] = make_user(db, "youthmgr@x.test", "mentor", None, now, roles=("youth_manager",))
    U["thu_mgr"] = make_user(db, "thu.mgr@x.test", "mentor", 4, now)
    U["thu_mentor"] = make_user(db, "thu.mentor@x.test", "mentor", 4, now)
    U["thu_youth"] = make_user(db, "thu.youth@x.test", "youth", 4, now)
    U["thu_trainee"] = make_user(db, "thu.trainee@x.test", "trainee", 4, now)
    U["thu_trainee2"] = make_user(db, "thu.trainee2@x.test", "trainee", 4, now)
    U["mon_vol"] = make_user(db, "mon.vol@x.test", "youth", 1, now)
    U["mon_trainee"] = make_user(db, "mon.trainee@x.test", "trainee", 1, now)
    U["sat_mgr"] = make_user(db, "sat.mgr@x.test", "mentor", 5, now)
    U["pending"] = make_user(db, "pending@x.test", None, None, now, status="pending")
    db.flush()
    accounts.set_day_managers(db, 4, [U["thu_mgr"].id], None, now)
    accounts.set_day_managers(db, 6, [U["sat_mgr"].id], None, now)
    # Full days (every shift type the day kind allows) so existing scenarios keep their meaning. The default for
    # new dates is evening only (decision 2026-10-16); tests of that default call create_day directly.
    days.generate_days(db, date(2026, 10, 11), date(2026, 10, 31), now, shift_kinds=None)
    db.commit()
    D = {d.local_date.isoformat(): d for d in db.query(days.ActivityDay).all()}
    return World(U, D)
