from datetime import timedelta

from sqlalchemy import select

from app.audit.models import AuditEvent
from tests.conftest import PASSWORD, Client


def signup(client: Client, email="new@x.test", **extra):
    body = {"email": email, "full_name": "מתנדב חדש", "password": PASSWORD, **extra}
    return client.post("/api/auth/signup", body)


def test_signup_is_pending_and_sees_nothing_but_own_profile(world):
    c = Client()
    assert signup(c).status_code == 201
    me = c.get("/api/auth/me").json()
    assert me["status"] == "pending" and me["level"] is None and not me["is_admin"]
    for url in ("/api/schedule", "/api/registrations", "/api/me/history", "/api/users/search?q=a",
                f"/api/schedule/{world.day('2026-10-15')}", "/api/manage/days"):
        assert c.get(url).status_code == 403, url
    assert c.get("/api/admin/users").status_code == 403


def test_signup_cannot_self_grant_roles(world):
    c = Client()
    r = signup(c, is_admin=True)
    assert r.status_code == 422
    r = signup(c, status="active")
    assert r.status_code == 422


def test_admin_approval_activates_with_level_and_day(world, db):
    c = Client()
    signup(c)
    admin = Client("admin@x.test")
    pending = admin.get("/api/admin/users?status=pending").json()
    new = next(u for u in pending if u["email"] == "new@x.test")
    r = admin.post(f"/api/admin/users/{new['id']}/approve", {"level": "trainee", "home_weekday": 4})
    assert r.status_code == 200 and r.json()["status"] == "active"
    # Same session, no re-login: access reflects the new state immediately.
    me = c.get("/api/auth/me").json()
    assert me["status"] == "active" and me["level"] == "trainee" and me["home_weekday"] == 4
    assert c.get("/api/schedule").status_code == 200
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "signup_approve")) is not None


def test_only_admin_can_approve_and_admin_api_is_hidden(world):
    c = Client()
    signup(c)
    for email in ("thu.mgr@x.test", "youthmgr@x.test", "thu.trainee@x.test"):
        assert Client(email).get("/api/admin/users").status_code == 404


def test_session_survives_closure_and_expires_after_30_idle_days(world, clock):
    c = Client("thu.trainee@x.test")
    clock.set(clock.now() + timedelta(days=29))
    assert c.get("/api/auth/me").status_code == 200  # meaningful activity refreshes the session
    clock.set(clock.now() + timedelta(days=29))
    assert c.get("/api/auth/me").status_code == 200
    clock.set(clock.now() + timedelta(days=30, minutes=1))
    assert c.get("/api/auth/me").status_code == 401


def test_background_polling_does_not_extend_session(world, clock):
    c = Client("thu.trainee@x.test")
    for _ in range(2):  # day 10 and day 20: polling works but must not refresh activity
        clock.set(clock.now() + timedelta(days=10))
        assert c.get("/api/me/notifications", headers={"X-MDA-Background": "1"}).status_code == 200
    clock.set(clock.now() + timedelta(days=10, minutes=1))  # 30 days since the last real activity
    assert c.get("/api/me/notifications", headers={"X-MDA-Background": "1"}).status_code == 401


def test_logout_revokes_server_session(world):
    c = Client("thu.trainee@x.test")
    stolen_cookie = c.http.cookies.get("mda_session")
    assert c.post("/api/auth/logout").status_code == 200
    other = Client()
    other.http.cookies.set("mda_session", stolen_cookie)
    assert other.get("/api/auth/me").status_code == 401


def test_csrf_required_for_mutations(world):
    c = Client("thu.trainee@x.test")
    r = c.http.put(f"/api/registrations/{world.day('2026-10-15')}", json={"choices": []})
    assert r.status_code == 403 and r.json()["code"] == "csrf_failed"


def test_login_rate_limit(world):
    c = Client()
    codes = [c.post("/api/auth/login", {"email": "ratelimit@x.test", "password": "wrong-password"}).status_code
             for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429


def test_youth_manager_appoints_day_managers_mentor_only(world, db):
    ym = Client("youthmgr@x.test")
    r = ym.put("/api/day-managers/1", {"user_ids": [world.uid("mon_vol")]})
    assert r.status_code == 409 and r.json()["code"] == "day_manager_must_be_mentor"
    r = ym.put("/api/day-managers/1", {"user_ids": [world.uid("thu_mentor")]})
    assert r.status_code == 200
    mentor = Client("thu.mentor@x.test")
    assert 1 in mentor.get("/api/auth/me").json()["managed_weekdays"]
    # Removal applies on the next request without re-login.
    ym.put("/api/day-managers/1", {"user_ids": []})
    assert mentor.get("/api/auth/me").json()["managed_weekdays"] == []
    # Day managers and volunteers cannot appoint.
    assert Client("thu.mgr@x.test").put("/api/day-managers/1", {"user_ids": []}).status_code == 404


def test_day_manager_changes_levels_only_within_scope(world):
    mgr = Client("thu.mgr@x.test")
    assert mgr.put(f"/api/manage/users/{world.uid('thu_trainee')}/level", {"level": "youth"}).status_code == 200
    assert mgr.put(f"/api/manage/users/{world.uid('mon_trainee')}/level", {"level": "youth"}).status_code == 403


def test_admin_can_edit_any_user_and_roles(world):
    admin = Client("admin@x.test")
    r = admin.patch(f"/api/admin/users/{world.uid('mon_vol')}", {"home_weekday": 4, "is_youth_manager": True})
    assert r.status_code == 200 and r.json()["home_weekday"] == 4 and r.json()["roles"] == ["youth_manager"]
    assert admin.patch(f"/api/admin/users/{world.uid('admin')}", {"is_admin": False}).status_code == 403


def test_password_hashing_is_bounded_to_protect_memory(monkeypatch):
    """At most `password_hash_concurrency` argon2 operations run at once (64 MiB each)."""
    import threading
    import time

    from app.accounts import security

    active, peak, lock = 0, 0, threading.Lock()
    real = security._hasher

    class CountingHasher:
        def hash(self, p):
            return real.hash(p)

        def verify(self, h, p):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return real.verify(h, p)

    h = security.hash_password("a-long-test-password")
    monkeypatch.setattr(security, "_hasher", CountingHasher())
    threads = [threading.Thread(target=security.verify_password, args=(h, "a-long-test-password")) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 2
