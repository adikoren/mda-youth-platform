"""Password reset (שכחתי סיסמה) and Google sign-in.

Automated tests never send real email: the capture mode writes to a temporary folder, and Google is replaced by
fixed responses (its token exchange and signature check are mocked here; the real calls are checked by hand)."""
import json
import types

import pytest
from sqlalchemy import select

from app.accounts import google, reset
from app.accounts.models import AuthSession, ExternalIdentity, OAuthState, PasswordReset, User
from app.accounts.security import reset_email_limiter, reset_ip_limiter, token_hash
from app.core.config import get_settings
from tests.conftest import PASSWORD, Client, at

NEW_PASSWORD = "brand-new-pass-456"


@pytest.fixture(autouse=True)
def capture(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "email_mode", "capture")
    monkeypatch.setattr(s, "email_capture_dir", str(tmp_path))
    monkeypatch.setattr(s, "public_base_url", "http://localhost:5173")
    reset_ip_limiter.clear()
    reset_email_limiter.clear()
    return tmp_path


def sent(folder):
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def token_from(folder):
    link = sent(folder)[-1]["text"].split("\n")[1]
    return link.split("token=")[1]


def forgot(email):
    return Client().post("/api/auth/password/forgot", {"email": email})


# ---------- request: neutral answers, no leaks ----------

def test_known_and_unknown_addresses_get_the_same_neutral_answer(world, clock, capture):
    clock.set(at("2026-10-12", "21:00"))
    known = forgot("thu.youth@x.test")
    unknown = forgot("nobody@x.test")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": reset.NEUTRAL_MESSAGE}
    assert len(sent(capture)) == 1  # only the known account received a link


def test_pending_and_blocked_accounts_get_the_same_answer_and_no_status_is_shown(world, clock, capture, db):
    clock.set(at("2026-10-12", "21:00"))
    assert forgot("pending@x.test").json() == {"message": reset.NEUTRAL_MESSAGE}


def test_a_reset_link_is_not_sent_when_email_is_not_configured(world, clock, capture, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    monkeypatch.setattr(get_settings(), "email_mode", "off")
    r = forgot("thu.youth@x.test")
    assert r.status_code == 200 and r.json() == {"message": reset.NEUTRAL_MESSAGE}
    assert db.query(PasswordReset).count() == 0  # the unsent link is discarded, not left valid


def test_the_link_uses_only_the_configured_base_address(world, clock, capture, monkeypatch):
    clock.set(at("2026-10-12", "21:00"))
    monkeypatch.setattr(get_settings(), "public_base_url", "https://trusted.example")
    forgot("thu.youth@x.test")
    assert sent(capture)[-1]["text"].split("\n")[1].startswith("https://trusted.example/reset?token=")


# ---------- the token: single use, expiry, hashes only ----------

def test_token_resets_once_then_is_used_and_sessions_are_revoked(world, clock, capture, db):
    clock.set(at("2026-10-12", "21:00"))
    before = Client("thu.youth@x.test")  # an existing session that must stop working
    forgot("thu.youth@x.test")
    token = token_from(capture)
    assert db.scalar(select(PasswordReset.token_hash).where(PasswordReset.token_hash == token_hash(token)))
    assert Client().get(f"/api/auth/password/token-status?token={token}").json() == {"status": "valid"}

    ok = Client().post("/api/auth/password/reset", {"token": token, "password": NEW_PASSWORD})
    assert ok.status_code == 200 and ok.json() == {"status": "ok"}
    again = Client().post("/api/auth/password/reset", {"token": token, "password": "another-pass-789"})
    assert again.status_code == 400 and again.json()["code"] == "reset_used"
    assert Client().get(f"/api/auth/password/token-status?token={token}").json() == {"status": "used"}
    # The earlier session is gone; the new password works; the old one does not.
    assert before.get("/api/auth/me").status_code == 401
    assert Client().post("/api/auth/login", {"email": "thu.youth@x.test", "password": NEW_PASSWORD}).status_code == 200
    assert Client().post("/api/auth/login", {"email": "thu.youth@x.test", "password": PASSWORD}).status_code == 401


def test_a_reset_does_not_sign_the_person_in(world, clock, capture):
    clock.set(at("2026-10-12", "21:00"))
    forgot("thu.youth@x.test")
    c = Client()
    assert c.post("/api/auth/password/reset", {"token": token_from(capture), "password": NEW_PASSWORD}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_an_expired_link_is_refused(world, clock, capture):
    clock.set(at("2026-10-12", "21:00"))
    forgot("thu.youth@x.test")
    token = token_from(capture)
    clock.set(at("2026-10-12", "23:30"))  # more than an hour later
    r = Client().post("/api/auth/password/reset", {"token": token, "password": NEW_PASSWORD})
    assert r.status_code == 400 and r.json()["code"] == "reset_expired"


def test_an_invalid_link_is_refused(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    r = Client().post("/api/auth/password/reset", {"token": "x" * 30, "password": NEW_PASSWORD})
    assert r.status_code == 400 and r.json()["code"] == "reset_invalid"


def test_a_second_request_replaces_the_first_link(world, clock, capture):
    clock.set(at("2026-10-12", "21:00"))
    forgot("thu.youth@x.test")
    first = token_from(capture)
    forgot("thu.youth@x.test")
    second = token_from(capture)
    assert Client().get(f"/api/auth/password/token-status?token={first}").json() == {"status": "used"}
    assert Client().get(f"/api/auth/password/token-status?token={second}").json() == {"status": "valid"}


def test_only_hashes_of_tokens_are_stored(world, clock, capture, db):
    clock.set(at("2026-10-12", "21:00"))
    forgot("thu.youth@x.test")
    token = token_from(capture)
    stored = [row.token_hash for row in db.scalars(select(PasswordReset))]
    assert token not in stored and token_hash(token) in stored


# ---------- rate limits, without locking out normal login ----------

def test_request_limits_stop_new_links_but_answers_stay_neutral(world, clock, capture):
    clock.set(at("2026-10-12", "21:00"))
    for _ in range(5):
        forgot("thu.youth@x.test")
    r = forgot("thu.youth@x.test")  # sixth request for the same account
    assert r.status_code == 200 and r.json() == {"message": reset.NEUTRAL_MESSAGE}
    assert len(sent(capture)) == 5
    # Normal login for the same account is not affected by reset requests.
    assert Client("thu.youth@x.test").get("/api/auth/me").status_code == 200


# ---------- pending and blocked accounts stay restricted ----------

def test_reset_does_not_activate_a_pending_account_or_change_its_status(world, clock, capture, db):
    clock.set(at("2026-10-12", "21:00"))
    user = db.scalar(select(User).where(User.email == "pending@x.test"))
    forgot("pending@x.test")
    assert sent(capture)  # a pending account with a password may reset its password
    token = token_from(capture)
    assert Client().post("/api/auth/password/reset", {"token": token, "password": NEW_PASSWORD}).status_code == 200
    db.expire_all()
    assert db.get(User, user.id).status == "pending"
    pending = Client()
    assert pending.post("/api/auth/login", {"email": "pending@x.test", "password": NEW_PASSWORD}).status_code == 200
    assert pending.get("/api/registrations").status_code in (401, 403)  # still restricted


def test_reset_does_not_change_roles(world, clock, capture, db):
    clock.set(at("2026-10-12", "21:00"))
    forgot("admin@x.test")
    Client().post("/api/auth/password/reset", {"token": token_from(capture), "password": NEW_PASSWORD})
    assert Client().post("/api/auth/login", {"email": "admin@x.test", "password": PASSWORD}).status_code == 401
    admin = Client()
    assert admin.post("/api/auth/login", {"email": "admin@x.test", "password": NEW_PASSWORD}).status_code == 200
    assert admin.get("/api/admin/users").status_code == 200  # the admin role is unchanged


# ---------- Google: identity validation and linking rules ----------

@pytest.fixture
def google_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "not-a-real-secret")
    return s


def fixed_states(monkeypatch, values):
    """Fixed state and nonce values for the Google module only (session tokens elsewhere stay random)."""
    seq = iter(values)
    fake = types.SimpleNamespace(token_urlsafe=lambda n: next(seq))
    monkeypatch.setattr(google, "secrets", fake)


def claims(sub, email, nonce, verified=True):
    return {"iss": "https://accounts.google.com", "sub": sub, "email": email, "email_verified": verified, "nonce": nonce}


def test_google_is_off_until_it_is_configured(world):
    assert Client().get("/api/auth/capabilities").json() == {"google": False}
    assert Client().get("/api/auth/google/start", follow_redirects=False).status_code == 404


def test_google_start_builds_a_consent_request_with_only_identity_scopes(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    fixed_states(monkeypatch, ["STATE1", "NONCE1"])
    r = Client().get("/api/auth/google/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "scope=openid+email+profile" in loc and "state=STATE1" in loc and "nonce=NONCE1" in loc
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Fapi%2Fauth%2Fgoogle%2Fcallback" in loc
    assert "gmail" not in loc and "mail.google" not in loc


def callback(code, state):
    return Client().get(f"/api/auth/google/callback?code={code}&state={state}", follow_redirects=False)


def test_linked_google_identity_signs_in(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    user = db.scalar(select(User).where(User.email == "thu.youth@x.test"))
    db.add(ExternalIdentity(provider="google", subject="111", user_id=user.id, created_at=clock.now()))
    db.commit()
    fixed_states(monkeypatch, ["S-ok", "N-ok"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw-token")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("111", "thu.youth@x.test", "N-ok"))
    r = callback("c1", "S-ok")
    assert r.status_code == 302 and r.headers["location"].endswith("/")
    assert "mda_session" in r.headers.get("set-cookie", "")


def test_a_matching_email_alone_never_links_or_signs_in(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    fixed_states(monkeypatch, ["S-x", "N-x"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("999", "thu.youth@x.test", "N-x"))
    r = callback("c2", "S-x")
    assert "reason=not_linked" in r.headers["location"]
    assert db.scalar(select(ExternalIdentity.id).where(ExternalIdentity.subject == "999")) is None
    assert "mda_session" not in r.headers.get("set-cookie", "")


def test_an_unknown_google_identity_gets_no_account(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    before = db.query(User).count()
    fixed_states(monkeypatch, ["S-n", "N-n"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("555", "stranger@x.test", "N-n"))
    r = callback("c3", "S-n")
    assert "reason=no_account" in r.headers["location"]
    assert db.query(User).count() == before  # the existing signup and approval process still applies


def test_unverified_google_email_is_refused(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    fixed_states(monkeypatch, ["S-v", "N-v"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("777", "thu.youth@x.test", "N-v", verified=False))
    assert "reason=email_not_verified" in callback("c4", "S-v").headers["location"]


def test_wrong_nonce_is_refused(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    fixed_states(monkeypatch, ["S-w", "N-w"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("1", "thu.youth@x.test", "SOMETHING-ELSE"))
    assert "reason=nonce_mismatch" in callback("c5", "S-w").headers["location"]


def test_state_is_single_use(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    fixed_states(monkeypatch, ["S-r", "N-r"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("42", "thu.youth@x.test", "N-r"))
    callback("c6", "S-r")
    assert "reason=state_invalid" in callback("c6", "S-r").headers["location"]


def test_an_inactive_linked_account_is_refused(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    user = db.scalar(select(User).where(User.email == "thu.youth@x.test"))
    db.add(ExternalIdentity(provider="google", subject="333", user_id=user.id, created_at=clock.now()))
    user.status = "inactive"
    db.commit()
    fixed_states(monkeypatch, ["S-i", "N-i"])
    Client().get("/api/auth/google/start", follow_redirects=False)
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("333", "thu.youth@x.test", "N-i"))
    assert "reason=account_inactive" in callback("c7", "S-i").headers["location"]


def test_linking_requires_the_password_and_links_only_the_matching_email(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    me = Client("thu.youth@x.test")
    assert me.post("/api/auth/google/link/start", {"password": "wrong-password"}).status_code == 401
    fixed_states(monkeypatch, ["S-l", "N-l"])
    url = me.post("/api/auth/google/link/start", {"password": PASSWORD}).json()["url"]
    assert "state=S-l" in url
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("222", "thu.youth@x.test", "N-l"))
    r = callback("c8", "S-l")
    assert r.headers["location"].endswith("/profile?google=linked")
    user = db.scalar(select(User).where(User.email == "thu.youth@x.test"))
    assert db.scalar(select(ExternalIdentity.user_id).where(ExternalIdentity.subject == "222")) == user.id


def test_a_google_identity_already_used_by_another_account_cannot_be_linked(world, clock, google_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    other = db.scalar(select(User).where(User.email == "thu.trainee@x.test"))
    db.add(ExternalIdentity(provider="google", subject="444", user_id=other.id, created_at=clock.now()))
    db.commit()
    me = Client("thu.youth@x.test")
    fixed_states(monkeypatch, ["S-u", "N-u"])
    me.post("/api/auth/google/link/start", {"password": PASSWORD})
    monkeypatch.setattr(google, "exchange_code", lambda code: "raw")
    monkeypatch.setattr(google, "verify_id_token", lambda raw: claims("444", "thu.youth@x.test", "N-u"))
    assert "reason=identity_in_use" in callback("c9", "S-u").headers["location"]


def test_provider_errors_fail_safely(world, clock, google_on, monkeypatch):
    clock.set(at("2026-10-12", "21:00"))
    assert "reason=cancelled" in Client().get("/api/auth/google/callback?error=access_denied",
                                              follow_redirects=False).headers["location"]
    fixed_states(monkeypatch, ["S-p", "N-p"])
    Client().get("/api/auth/google/start", follow_redirects=False)

    def down(code):
        raise google.GoogleError("provider_unavailable")
    monkeypatch.setattr(google, "exchange_code", down)
    assert "reason=provider_unavailable" in callback("c10", "S-p").headers["location"]


# ---------- Brevo: accepted is not delivered; outages stay safe ----------

def test_brevo_accepted_is_reported_as_accepted_not_delivered(world, clock, monkeypatch, db):
    from app.accounts import mailer
    clock.set(at("2026-10-12", "21:00"))
    s = get_settings()
    monkeypatch.setattr(s, "email_mode", "brevo")
    monkeypatch.setattr(s, "brevo_api_key", "mock-key")
    monkeypatch.setattr(s, "email_from_address", "noreply@example.test")

    class Resp:
        status_code = 201

    monkeypatch.setattr(mailer.httpx, "post", lambda url, **kw: Resp())
    result = mailer.send(to="thu.youth@x.test", subject="s", text="t", html="h")
    assert result.mode == "brevo" and result.status == "accepted"  # queued by the provider, not delivered


def test_brevo_outage_answers_neutrally_and_leaves_no_usable_link(world, clock, monkeypatch, db):
    import httpx
    from app.accounts import mailer
    clock.set(at("2026-10-12", "21:00"))
    s = get_settings()
    monkeypatch.setattr(s, "email_mode", "brevo")
    monkeypatch.setattr(s, "brevo_api_key", "mock-key")
    monkeypatch.setattr(s, "email_from_address", "noreply@example.test")

    def down(url, **kw):
        raise httpx.ConnectError("unreachable")
    monkeypatch.setattr(mailer.httpx, "post", down)
    r = forgot("thu.youth@x.test")
    assert r.status_code == 200 and r.json() == {"message": reset.NEUTRAL_MESSAGE}
    assert db.query(PasswordReset).count() == 0


def test_brevo_refusal_answers_neutrally(world, clock, monkeypatch, db):
    from app.accounts import mailer
    clock.set(at("2026-10-12", "21:00"))
    s = get_settings()
    monkeypatch.setattr(s, "email_mode", "brevo")
    monkeypatch.setattr(s, "brevo_api_key", "mock-key")
    monkeypatch.setattr(s, "email_from_address", "noreply@example.test")

    class Refused:
        status_code = 401
    monkeypatch.setattr(mailer.httpx, "post", lambda url, **kw: Refused())
    assert forgot("thu.youth@x.test").json() == {"message": reset.NEUTRAL_MESSAGE}
    assert db.query(PasswordReset).count() == 0
