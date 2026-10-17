"""Web Push (standard, VAPID) sending for the test notification. Only the server holds the private key.

Safety rules (decision of 2026-10-17):
- A subscription endpoint must be an https URL on a known browser push service. Nothing else is ever contacted,
  so a stored endpoint cannot be used to reach arbitrary hosts (SSRF).
- Keys must have the base64url shape of a P-256 public key and an auth secret.
- A permanent provider answer (404/410) removes the subscription. Transient failures keep it.
- Logs never contain endpoints or keys. A push service accepting a request is NOT a delivery confirmation.
"""
import json
import re
from enum import Enum
from urllib.parse import urlsplit

from app.core.config import get_settings

# Hosts of the browser push services (Chrome/Android, Firefox, Safari, Edge/Windows).
_ALLOWED_EXACT = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
_ALLOWED_SUFFIX = (".notify.windows.com", ".push.services.mozilla.com")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


class PushResult(str, Enum):
    ACCEPTED = "accepted"        # the push service accepted the request; delivery is NOT confirmed
    GONE = "gone"                # permanent: 404/410, the subscription is no longer valid
    TRANSIENT = "transient"      # temporary failure (network, 429, 5xx): keep the subscription
    REJECTED = "rejected"        # other permanent error (e.g. 400/401/403): keep it, report it
    NOT_CONFIGURED = "not_configured"


def valid_endpoint(endpoint: str) -> bool:
    if not endpoint or len(endpoint) > 1024:
        return False
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return False
    host = (parts.hostname or "").lower()
    return host in _ALLOWED_EXACT or host.endswith(_ALLOWED_SUFFIX)


def valid_keys(p256dh: str, auth: str) -> bool:
    return (60 <= len(p256dh) <= 120 and bool(_B64URL.match(p256dh))
            and 16 <= len(auth) <= 40 and bool(_B64URL.match(auth)))


def public_key() -> str | None:
    key = get_settings().vapid_public_key.strip()
    return key or None


def configured() -> bool:
    s = get_settings()
    return bool(s.vapid_public_key.strip() and s.vapid_private_key_path)


def send(endpoint: str, p256dh: str, auth: str, payload: dict) -> PushResult:
    """One push to one subscription. The payload carries no personal data."""
    if not configured():
        return PushResult.NOT_CONFIGURED
    from pywebpush import WebPushException, webpush
    s = get_settings()
    try:
        webpush(subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=json.dumps(payload), vapid_private_key=s.vapid_private_key_path,
                vapid_claims={"sub": s.vapid_subject}, timeout=10)
        return PushResult.ACCEPTED
    except WebPushException as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code in (404, 410):
            return PushResult.GONE
        if code is None or code == 429 or code >= 500:
            return PushResult.TRANSIENT
        return PushResult.REJECTED
    except Exception:  # network errors: transient by definition here; the endpoint itself is never logged
        return PushResult.TRANSIENT
