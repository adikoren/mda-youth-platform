"""Google sign-in (OpenID Connect, authorization code flow). Scopes: openid, email, profile only.

Identity is the provider's stable `sub`, stored in external_identities. Email is never a permanent identifier and is
never enough to sign in or link: an existing account is linked only from its own profile, after the person signs in
with the password (re-authentication). A Google identity without a linked account gets no account: the existing
registration and manager approval apply.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.accounts.models import ExternalIdentity, OAuthState, User
from app.accounts.security import normalize_email
from app.audit.service import record
from app.core.config import get_settings

PROVIDER = "google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def enabled() -> bool:
    s = get_settings()
    return bool(s.google_client_id and s.google_client_secret)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _redirect_uri() -> str:
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        raise GoogleError("not_configured")
    return f"{base}/api/auth/google/callback"


def start(db: Session, now: datetime, purpose: str, user_id=None) -> str:
    """Creates a single-use state and nonce and returns the Google consent URL."""
    if not enabled():
        raise GoogleError("not_configured")
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.add(OAuthState(state_hash=_hash(state), nonce_hash=_hash(nonce), purpose=purpose, user_id=user_id,
                      created_at=now, expires_at=now + timedelta(minutes=get_settings().oauth_state_minutes)))
    db.commit()
    return AUTH_URL + "?" + urlencode({
        "client_id": get_settings().google_client_id, "redirect_uri": _redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
    })


def exchange_code(code: str) -> str:
    """Trades the code for the id_token at Google. Returns the raw id_token."""
    s = get_settings()
    try:
        r = httpx.post(TOKEN_URL, timeout=10, data={
            "code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret,
            "redirect_uri": _redirect_uri(), "grant_type": "authorization_code"})
    except httpx.HTTPError:
        raise GoogleError("provider_unavailable") from None
    if r.status_code != 200 or "id_token" not in r.json():
        raise GoogleError("token_exchange_failed")
    return r.json()["id_token"]


def verify_id_token(raw: str) -> dict:
    """Checks signature, issuer, audience and expiry (google-auth). Raises GoogleError on any failure."""
    try:
        claims = google_id_token.verify_oauth2_token(raw, google_requests.Request(), get_settings().google_client_id)
    except ValueError:
        raise GoogleError("invalid_token") from None
    if claims.get("iss") not in ISSUERS:
        raise GoogleError("invalid_token")
    return claims


def consume_state(db: Session, state: str, now: datetime) -> OAuthState:
    """Atomic single use: the state can be consumed once, even with two concurrent callbacks."""
    row = db.execute(
        update(OAuthState)
        .where(OAuthState.state_hash == _hash(state), OAuthState.used_at.is_(None), OAuthState.expires_at > now)
        .values(used_at=now)
        .returning(OAuthState.id, OAuthState.nonce_hash, OAuthState.purpose, OAuthState.user_id)
    ).first()
    if row is None:
        db.rollback()
        raise GoogleError("state_invalid")
    db.commit()
    return row


@dataclass(frozen=True)
class Outcome:
    purpose: str
    user: User | None  # set for a sign-in or a completed link


def complete(db: Session, code: str, state: str, now: datetime) -> Outcome:
    st = consume_state(db, state, now)
    claims = verify_id_token(exchange_code(code))
    if _hash(claims.get("nonce", "")) != st.nonce_hash:
        raise GoogleError("nonce_mismatch")
    if claims.get("email_verified") is not True or not claims.get("email") or not claims.get("sub"):
        raise GoogleError("email_not_verified")
    sub, email = str(claims["sub"]), normalize_email(claims["email"])
    linked = db.scalar(select(ExternalIdentity).where(ExternalIdentity.provider == PROVIDER,
                                                      ExternalIdentity.subject == sub))

    if st.purpose == "link":
        owner = db.get(User, st.user_id)
        if owner is None or owner.email != email:
            raise GoogleError("link_email_mismatch")
        if linked is not None and linked.user_id != owner.id:
            raise GoogleError("identity_in_use")
        if linked is None:
            db.add(ExternalIdentity(provider=PROVIDER, subject=sub, user_id=owner.id, created_at=now))
            record(db, actor_id=owner.id, action="google_linked", object_type="user", object_id=owner.id, now=now)
            db.commit()
        return Outcome(purpose="link", user=owner)

    if linked is None:
        # Not linked: no account is created and no account is linked by email. The person must use the usual path.
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            raise GoogleError("not_linked")
        raise GoogleError("no_account")
    user = db.get(User, linked.user_id)
    if user is None or user.status == "inactive":
        raise GoogleError("account_inactive")
    return Outcome(purpose="login", user=user)
