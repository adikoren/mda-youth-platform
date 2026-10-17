"""Web Push (decision of 2026-10-17): subscriptions belong to an authenticated account, one row per device, no
cross-account reuse, the self-test goes only to the requester's own device and is rate-limited, permanent
provider answers remove a subscription, transient ones do not, and endpoints/keys never come back in responses."""
import base64
import os
from datetime import timedelta

from sqlalchemy import func, select

from app.notifications import push
from app.notifications.models import PushSubscription
from app.notifications.push_router import TEST_PAYLOAD
from tests.conftest import Client, at

EP_A = "https://fcm.googleapis.com/fcm/send/device-a-0000000000000000000000000000000000000000"
EP_B = "https://updates.push.services.mozilla.com/wpush/v2/device-b-000000000000000000000000000000"
P256DH = base64.urlsafe_b64encode(b"\x04" + b"\x11" * 64).decode().rstrip("=")  # shape-valid public key
AUTH = base64.urlsafe_b64encode(b"\x22" * 16).decode().rstrip("=")
KEYS = {"p256dh": P256DH, "auth": AUTH}


def configured(monkeypatch, sent: list, result=push.PushResult.ACCEPTED):
    monkeypatch.setattr(push, "configured", lambda: True)

    def fake_send(endpoint, p256dh, auth, payload):
        sent.append((endpoint, payload))
        return result
    monkeypatch.setattr(push, "send", fake_send)


def rows(db, endpoint=None):
    db.expire_all()
    q = select(PushSubscription)
    if endpoint:
        q = q.where(PushSubscription.endpoint == endpoint)
    return db.scalars(q).all()


def test_config_exposes_only_the_public_key(world):
    body = Client().get("/api/push/config").json()
    assert set(body) == {"public_key", "configured"}


def test_subscription_management_requires_sign_in(world):
    anon = Client()
    assert anon.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS}).status_code in (401, 403)
    assert anon.post("/api/push/test", {"endpoint": EP_A}).status_code in (401, 403)


def test_subscribe_is_idempotent_per_device_and_does_not_echo_the_endpoint(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")
    r = vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    assert r.status_code == 200 and r.json() == {"registered": True}
    assert EP_A not in r.text and P256DH not in r.text  # nothing sensitive comes back
    vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    assert len(rows(db, EP_A)) == 1
    assert vol.post("/api/push/subscriptions/check", {"endpoint": EP_A}).json() == {"registered_to_me": True}


def test_invalid_endpoints_and_keys_are_refused(world):
    vol = Client("thu.youth@x.test")
    for bad in ("http://fcm.googleapis.com/x", "https://evil.example.test/x", "https://user@fcm.googleapis.com/x",
                "https://fcm.googleapis.com:8443/x", "javascript:alert(1)"):
        assert vol.post("/api/push/subscriptions", {"endpoint": bad, "keys": KEYS}).status_code == 400, bad
    bad_keys = {"p256dh": "short", "auth": AUTH}
    assert vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": bad_keys}).status_code == 400


def test_switching_accounts_on_a_device_moves_the_subscription_no_duplicates(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    Client("thu.youth@x.test").post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    other = Client("thu.trainee@x.test")
    assert other.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS}).status_code == 200
    rows_now = rows(db, EP_A)
    assert len(rows_now) == 1 and str(rows_now[0].user_id) == str(world.uid("thu_trainee"))  # one owner at a time
    assert Client("thu.youth@x.test").post("/api/push/subscriptions/check", {"endpoint": EP_A}).json() == {"registered_to_me": False}


def test_removal_detaches_only_the_owners_device(world, db, clock):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")
    vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    assert Client("thu.trainee@x.test").post("/api/push/subscriptions/remove", {"endpoint": EP_A}).json() == {"removed": False}
    assert vol.post("/api/push/subscriptions/remove", {"endpoint": EP_A}).json() == {"removed": True}
    assert rows(db, EP_A) == []


def test_self_test_goes_only_to_the_requesters_own_device(world, db, clock, monkeypatch):
    clock.set(at("2026-10-12", "21:00"))
    Client("thu.youth@x.test").post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    sent: list = []
    configured(monkeypatch, sent)
    intruder = Client("thu.trainee@x.test")
    assert intruder.post("/api/push/test", {"endpoint": EP_A}).status_code == 404  # not their device
    assert sent == []
    r = Client("thu.youth@x.test").post("/api/push/test", {"endpoint": EP_A})
    assert r.status_code == 200 and r.json() == {"result": "accepted_by_push_service", "delivery_confirmed": False}
    assert len(sent) == 1 and sent[0][0] == EP_A
    assert sent[0][1] == TEST_PAYLOAD and TEST_PAYLOAD["title"] == "מד״א"
    assert TEST_PAYLOAD["body"] == "ההתראות שלך פועלות ❤️"
    assert set(TEST_PAYLOAD) <= {"title", "body", "url", "tag"}  # generic: no schedule or personal data


def test_self_test_is_rate_limited(world, db, clock, monkeypatch):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")
    vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    sent: list = []
    configured(monkeypatch, sent)
    assert vol.post("/api/push/test", {"endpoint": EP_A}).status_code == 200
    assert vol.post("/api/push/test", {"endpoint": EP_A}).status_code == 429
    clock.set(clock.now() + timedelta(seconds=31))
    assert vol.post("/api/push/test", {"endpoint": EP_A}).status_code == 200
    assert len(sent) == 2


def test_a_permanent_provider_answer_removes_the_subscription(world, db, clock, monkeypatch):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")
    vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    configured(monkeypatch, [], result=push.PushResult.GONE)
    r = vol.post("/api/push/test", {"endpoint": EP_A})
    assert r.json()["result"] == "subscription_expired"
    assert rows(db, EP_A) == []


def test_a_transient_failure_keeps_the_subscription(world, db, clock, monkeypatch):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")
    vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    configured(monkeypatch, [], result=push.PushResult.TRANSIENT)
    assert vol.post("/api/push/test", {"endpoint": EP_A}).json()["result"] == "temporarily_unavailable"
    assert len(rows(db, EP_A)) == 1


def test_test_action_reports_not_configured_honestly(world, clock):
    clock.set(at("2026-10-12", "21:00"))
    vol = Client("thu.youth@x.test")
    vol.post("/api/push/subscriptions", {"endpoint": EP_A, "keys": KEYS})
    assert vol.post("/api/push/test", {"endpoint": EP_A}).status_code == 503


def test_endpoint_allowlist_blocks_unknown_hosts():
    assert push.valid_endpoint(EP_A) and push.valid_endpoint(EP_B)
    assert push.valid_endpoint("https://web.push.apple.com/abc")
    assert not push.valid_endpoint("https://169.254.169.254/latest/meta-data")
    assert not push.valid_endpoint("https://localhost/x")
    assert not push.valid_endpoint("https://fcm.googleapis.com.evil.test/x")
