"""Password reset (שכחתי סיסמה). Decisions: neutral answers, single-use tokens stored as hashes, one hour,
atomic consumption, all sessions revoked on success. A reset never changes status, roles or approval, and never
signs the person in. Rate limits are per IP and per account identifier, without locking out normal logins."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.accounts import mailer
from app.accounts.models import AuthSession, PasswordReset, User
from app.accounts.security import hash_password, new_token, normalize_email, reset_email_limiter, reset_ip_limiter, token_hash
from app.audit.service import record
from app.core.config import get_settings

log = logging.getLogger("mda.reset")

NEUTRAL_MESSAGE = "אם קיים חשבון עם כתובת המייל הזו, יישלח אליו קישור לאיפוס סיסמה."


def _reset_link(token: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    if not base:
        raise mailer.MailNotConfigured("MDA_PUBLIC_BASE_URL is not set")
    return f"{base}/reset?token={token}"


def request_reset(db: Session, email: str, ip: str, now: datetime) -> None:
    """Always returns None to the caller: the HTTP answer never depends on what happened here."""
    email = normalize_email(email)
    if not reset_ip_limiter.allow(ip) or not reset_email_limiter.allow(email):
        return
    user = db.scalar(select(User).where(User.email == email))
    if user is None or user.password_hash is None:
        return
    # A new request replaces any earlier unused link for this account.
    db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
               .values(used_at=now))
    token = new_token()
    db.add(PasswordReset(user_id=user.id, token_hash=token_hash(token), created_at=now,
                         expires_at=now + timedelta(minutes=get_settings().reset_token_minutes)))
    db.flush()
    try:
        mailer.send(
            to=user.email,
            subject="איפוס סיסמה — נוער מדא",
            text=f"לאיפוס הסיסמה פתחו את הקישור (תקף לשעה אחת):\n{_reset_link(token)}\n\nאם לא ביקשתם, אפשר להתעלם.",
            html=f'<p dir="rtl">לאיפוס הסיסמה לחצו על הקישור (תקף לשעה אחת):</p>'
                 f'<p dir="rtl"><a href="{_reset_link(token)}">איפוס סיסמה</a></p>'
                 f'<p dir="rtl">אם לא ביקשתם, אפשר להתעלם.</p>',
        )
    except mailer.MailNotConfigured as exc:
        # The link was not sent, so it must not stay valid. Only the reason is logged, never the token.
        db.rollback()
        log.warning("password reset not sent: %s", exc)
        return
    record(db, actor_id=user.id, action="password_reset_requested", object_type="user", object_id=user.id, now=now)
    db.commit()


def token_status(db: Session, token: str, now: datetime) -> str:
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == token_hash(token)))
    if row is None:
        return "invalid"
    if row.used_at is not None:
        return "used"
    if row.expires_at <= now:
        return "expired"
    return "valid"


def reset_password(db: Session, token: str, new_password: str, now: datetime) -> str:
    """Returns "ok" or the reason the link cannot be used: "used", "expired" or "invalid"."""
    user_id = db.execute(
        update(PasswordReset)
        .where(PasswordReset.token_hash == token_hash(token), PasswordReset.used_at.is_(None),
               PasswordReset.expires_at > now)
        .values(used_at=now)
        .returning(PasswordReset.user_id)
    ).scalar()
    if user_id is None:
        db.rollback()
        return token_status(db, token, now)
    user = db.get(User, user_id)
    user.password_hash = hash_password(new_password)
    # Every session and every other outstanding link for this account stops working.
    db.execute(update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
               .values(revoked_at=now))
    db.execute(update(PasswordReset).where(PasswordReset.user_id == user_id, PasswordReset.used_at.is_(None))
               .values(used_at=now))
    record(db, actor_id=user_id, action="password_reset_completed", object_type="user", object_id=user_id, now=now)
    db.commit()
    return "ok"
