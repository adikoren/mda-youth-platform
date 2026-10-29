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


# ---------- Gmail API: accepted is not delivered; outages and refusals stay safe ----------

GMAIL_CLIENT_SECRET = "not-a-real-client-secret"
GMAIL_REFRESH_TOKEN = "not-a-real-refresh-token"


@pytest.fixture
def gmail_on(monkeypatch):
    from app.accounts import mailer
    s = get_settings()
    monkeypatch.setattr(s, "email_mode", "gmail")
    monkeypatch.setattr(s, "gmail_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(s, "gmail_client_secret", GMAIL_CLIENT_SECRET)
    monkeypatch.setattr(s, "gmail_refresh_token", GMAIL_REFRESH_TOKEN)
    monkeypatch.setattr(s, "email_from_address", "mda.youth.test@gmail.com")
    mailer._token_cache.update(value="", expires_at=0.0)
    return mailer


def fake_gmail(monkeypatch, mailer, send_status=200, token_status=200, token_error=None):
    """Replaces the two Google calls. Returns the list of requests that reached the fake."""
    seen = []

    class Resp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body

        def json(self):
            return self._body

    def post(url, **kw):
        seen.append((url, kw))
        if token_error is not None:
            raise token_error
        if url == mailer.GOOGLE_TOKEN_URL:
            return Resp(token_status, {"access_token": "not-a-real-access-token", "expires_in": 3600})
        return Resp(send_status, {"id": "msg-1", "threadId": "thread-1"})

    monkeypatch.setattr(mailer.httpx, "post", post)
    return seen


def test_gmail_accepted_is_reported_as_accepted_not_delivered(world, clock, gmail_on, monkeypatch, db):
    seen = fake_gmail(monkeypatch, gmail_on)
    clock.set(at("2026-10-12", "21:00"))
    result = gmail_on.send(to="thu.youth@x.test", subject="s", text="t", html="h")
    assert result.mode == "gmail" and result.status == "accepted"  # queued by Gmail, not delivered
    assert [url for url, _ in seen] == [gmail_on.GOOGLE_TOKEN_URL, gmail_on.GMAIL_SEND_URL]


def test_gmail_message_is_utf8_from_the_account_with_the_hebrew_sender_name(world, clock, gmail_on, monkeypatch):
    import base64
    from email import message_from_bytes, policy
    seen = fake_gmail(monkeypatch, gmail_on)
    gmail_on.send(to="thu.youth@x.test", subject="איפוס סיסמה", text="קישור: https://x.test/reset?token=t",
                  html="<p>קישור</p>")
    send_call = next(kw for url, kw in seen if url == gmail_on.GMAIL_SEND_URL)
    assert send_call["headers"] == {"Authorization": "Bearer not-a-real-access-token"}
    msg = message_from_bytes(base64.urlsafe_b64decode(send_call["json"]["raw"]), policy=policy.default)
    assert str(msg["Subject"]) == "איפוס סיסמה"
    assert str(msg["To"]) == "thu.youth@x.test"
    assert "נוער מדא" in str(msg["From"]) and "mda.youth.test@gmail.com" in str(msg["From"])
    assert msg.get_body(("plain",)).get_content().startswith("קישור")
    assert msg.get_body(("html",)).get_content().startswith("<p>")


def test_gmail_access_token_is_reused_until_it_expires(world, clock, gmail_on, monkeypatch):
    seen = fake_gmail(monkeypatch, gmail_on)
    gmail_on.send(to="a@x.test", subject="s", text="t", html="h")
    gmail_on.send(to="b@x.test", subject="s", text="t", html="h")
    assert sum(1 for url, _ in seen if url == gmail_on.GOOGLE_TOKEN_URL) == 1


def test_refused_token_refresh_is_safe_and_does_not_leak_the_secret(world, clock, gmail_on, monkeypatch):
    fake_gmail(monkeypatch, gmail_on, token_status=400)
    with pytest.raises(gmail_on.MailNotConfigured) as exc:
        gmail_on.send(to="a@x.test", subject="s", text="t", html="h")
    assert "HTTP 400" in str(exc.value)
    assert GMAIL_CLIENT_SECRET not in str(exc.value) and GMAIL_REFRESH_TOKEN not in str(exc.value)


def test_gmail_missing_configuration_is_refused_before_any_call(world, clock, gmail_on, monkeypatch):
    seen = fake_gmail(monkeypatch, gmail_on)
    monkeypatch.setattr(get_settings(), "gmail_refresh_token", "")
    with pytest.raises(gmail_on.MailNotConfigured):
        gmail_on.send(to="a@x.test", subject="s", text="t", html="h")
    assert seen == []


def test_gmail_outage_answers_neutrally_and_leaves_no_usable_link(world, clock, gmail_on, monkeypatch, db):
    import httpx
    clock.set(at("2026-10-12", "21:00"))
    fake_gmail(monkeypatch, gmail_on, token_error=httpx.ConnectError("unreachable"))
    r = forgot("thu.youth@x.test")
    assert r.status_code == 200 and r.json() == {"message": reset.NEUTRAL_MESSAGE}
    assert db.query(PasswordReset).count() == 0


def test_gmail_refusal_answers_neutrally(world, clock, gmail_on, monkeypatch, db):
    clock.set(at("2026-10-12", "21:00"))
    fake_gmail(monkeypatch, gmail_on, send_status=403)
    assert forgot("thu.youth@x.test").json() == {"message": reset.NEUTRAL_MESSAGE}
    assert db.query(PasswordReset).count() == 0


# ---------- Mailjet: accepted is not delivered; refusals and outages stay safe ----------

MAILJET_KEY = "not-a-real-mailjet-key"
MAILJET_SECRET = "not-a-real-mailjet-secret"


@pytest.fixture
def mailjet_on(monkeypatch):
    from app.accounts import mailer
    s = get_settings()
    monkeypatch.setattr(s, "email_mode", "mailjet")
    monkeypatch.setattr(s, "mailjet_api_key", MAILJET_KEY)
    monkeypatch.setattr(s, "mailjet_api_secret", MAILJET_SECRET)
    monkeypatch.setattr(s, "email_from_address", "mda101youth@gmail.com")
    return mailer


def fake_mailjet(monkeypatch, mailer, status=200, message_status="success"):
    seen = []

    class Resp:
        status_code = status

        def json(self):
            return {"Messages": [{"Status": message_status}]}

    def post(url, **kw):
        seen.append((url, kw))
        return Resp()

    monkeypatch.setattr(mailer.httpx, "post", post)
    return seen


def test_mailjet_accepted_is_reported_as_accepted_not_delivered(world, clock, mailjet_on, monkeypatch, db):
    seen = fake_mailjet(monkeypatch, mailjet_on)
    result = mailjet_on.send(to="thu.youth@x.test", subject="איפוס סיסמה", text="קישור", html="<p>קישור</p>")
    assert result.mode == "mailjet" and result.status == "accepted"  # queued by Mailjet, not delivered
    url, kw = seen[0]
    assert url == mailjet_on.MAILJET_URL
    assert kw["auth"] == (MAILJET_KEY, MAILJET_SECRET)
    message = kw["json"]["Messages"][0]
    assert message["From"] == {"Email": "mda101youth@gmail.com", "Name": "נוער מדא"}
    assert message["To"] == [{"Email": "thu.youth@x.test"}]
    assert message["Subject"] == "איפוס סיסמה"
    assert message["TextPart"] == "קישור" and message["HTMLPart"] == "<p>קישור</p>"


def test_mailjet_refusal_is_safe_and_does_not_leak_the_secret(world, clock, mailjet_on, monkeypatch):
    fake_mailjet(monkeypatch, mailjet_on, status=401, message_status="error")
    with pytest.raises(mailjet_on.MailNotConfigured) as exc:
        mailjet_on.send(to="a@x.test", subject="s", text="t", html="h")
    assert "HTTP 401" in str(exc.value)
    assert MAILJET_KEY not in str(exc.value) and MAILJET_SECRET not in str(exc.value)


def test_mailjet_error_status_inside_a_200_is_a_refusal(world, clock, mailjet_on, monkeypatch):
    fake_mailjet(monkeypatch, mailjet_on, status=200, message_status="error")
    with pytest.raises(mailjet_on.MailNotConfigured):
        mailjet_on.send(to="a@x.test", subject="s", text="t", html="h")


def test_mailjet_missing_configuration_is_refused_before_any_call(world, clock, mailjet_on, monkeypatch):
    seen = fake_mailjet(monkeypatch, mailjet_on)
    monkeypatch.setattr(get_settings(), "mailjet_api_secret", "")
    with pytest.raises(mailjet_on.MailNotConfigured):
        mailjet_on.send(to="a@x.test", subject="s", text="t", html="h")
    assert seen == []


def test_mailjet_outage_answers_neutrally_and_leaves_no_usable_link(world, clock, mailjet_on, monkeypatch, db):
    import httpx
    clock.set(at("2026-10-12", "21:00"))

    def down(url, **kw):
        raise httpx.ConnectError("unreachable")
    monkeypatch.setattr(mailjet_on.httpx, "post", down)
    r = forgot("thu.youth@x.test")
    assert r.status_code == 200 and r.json() == {"message": reset.NEUTRAL_MESSAGE}
    assert db.query(PasswordReset).count() == 0
