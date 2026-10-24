"""FastAPI integration: session cookie -> trusted Actor, CSRF double-submit check, clock."""
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.clock import Clock, get_clock
from app.access.context import Actor, load_actor
from app.accounts.models import AuthSession
from app.accounts.security import token_hash
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, forbidden

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
BACKGROUND_HEADER = "X-MDA-Background"  # polling requests: must not extend the session


def now_dep(clock: Clock = Depends(get_clock)) -> datetime:
    return clock.now()


def csrf_protect(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    s = get_settings()
    cookie = request.cookies.get(s.csrf_cookie_name)
    header = request.headers.get("X-CSRF-Token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise forbidden("csrf_failed", "פג תוקף הטופס, נא לרענן את הדף")


def session_is_live(sess: AuthSession, now: datetime) -> bool:
    idle = timedelta(days=get_settings().session_idle_days)
    return sess.revoked_at is None and now - sess.last_activity_at < idle


def current_session(request: Request, db: Session, now: datetime) -> AuthSession | None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if sess is None or not session_is_live(sess, now):
        return None
    return sess


def get_actor(request: Request, db: Session = Depends(get_db), now: datetime = Depends(now_dep)) -> Actor:
    """Any logged-in account, including pending ones (they may only see their own profile)."""
    sess = current_session(request, db, now)
    if sess is None:
        raise AppError(401, "not_authenticated", "יש להתחבר")
    actor = load_actor(db, sess.user_id, now)
    if actor is None or actor.status == "inactive":
        raise AppError(401, "not_authenticated", "יש להתחבר")
    is_background = request.headers.get(BACKGROUND_HEADER) == "1"
    touch_after = timedelta(minutes=get_settings().session_touch_minutes)
    if not is_background and now - sess.last_activity_at >= touch_after:
        sess.last_activity_at = now
        db.commit()
    return actor


def get_active_actor(actor: Actor = Depends(get_actor)) -> Actor:
    if not actor.is_active:
        raise forbidden("account_not_active", "החשבון ממתין לאישור מנהל")
    return actor
