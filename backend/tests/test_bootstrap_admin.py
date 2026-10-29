"""Admin account from the environment: created on first run, password set on later runs, short passwords refused.
The command reads MDA_ADMIN_EMAIL / MDA_ADMIN_PASSWORD / MDA_ADMIN_NAME; nothing is stored in the repository."""
import pytest
from sqlalchemy import select

from app import cli
from app.accounts.models import AuthorityRole, User
from app.accounts.security import verify_password
from app.core.db import session_factory
from tests.conftest import at, FixedClock  # noqa: F401  (clock fixture is in conftest)


def run(monkeypatch, email, password, name="Admin Tester"):
    monkeypatch.setenv("MDA_ADMIN_EMAIL", email)
    monkeypatch.setenv("MDA_ADMIN_PASSWORD", password)
    monkeypatch.setenv("MDA_ADMIN_NAME", name)
    cli.cmd_bootstrap_admin(None)


def test_creates_then_updates_the_password_and_keeps_admin(clock, db, monkeypatch, capsys):
    clock.set(at("2026-10-24", "12:00"))
    run(monkeypatch, "Admin@Example.Test", "first-password")
    assert "admin created" in capsys.readouterr().out
    run(monkeypatch, "admin@example.test", "second-password")
    assert "admin updated" in capsys.readouterr().out
    db.expire_all()
    user = db.scalar(select(User).where(User.email == "admin@example.test"))
    assert user.status == "active"
    assert verify_password(user.password_hash, "second-password")
    assert not verify_password(user.password_hash, "first-password")
    assert db.scalar(select(AuthorityRole.id).where(AuthorityRole.user_id == user.id,
                                                    AuthorityRole.role == "admin", AuthorityRole.valid_to.is_(None)))


def test_short_password_is_refused(clock, db, monkeypatch):
    clock.set(at("2026-10-24", "12:00"))
    with pytest.raises(SystemExit):
        run(monkeypatch, "short@example.test", "1234567")  # 7 characters: below the minimum of 8
    db.expire_all()
    assert db.scalar(select(User.id).where(User.email == "short@example.test")) is None
