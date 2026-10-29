"""Routes for password reset and Google sign-in. They reuse the existing session and CSRF protections."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.accounts import google, reset
from app.accounts.models import User
from app.accounts.router import EMAIL_PATTERN, _issue_session, rate_limiter
from app.accounts.security import MIN_PASSWORD_LENGTH, verify_password
from app.audit.service import record
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, not_found

router = APIRouter(prefix="/api/auth", tags=["auth"])

RESET_MESSAGES = {
    "used": "הקישור כבר נוצל. אפשר לבקש קישור חדש.",
    "expired": "הקישור פג תוקף. אפשר לבקש קישור חדש.",
    "invalid": "הקישור אינו תקין. אפשר לבקש קישור חדש.",
}


class ForgotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)


class ResetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class PasswordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(max_length=200)


@router.get("/capabilities")
def capabilities():
    """Which sign-in options are configured. Shows nothing secret."""
    return {"google": google.enabled()}


@router.post("/password/forgot")
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    ip = request.client.host if request.client else "?"
    reset.request_reset(db, body.email, ip, now)
    # The same answer whatever happened: unknown address, pending or blocked account, limit reached, or no mail.
    return {"message": reset.NEUTRAL_MESSAGE}


@router.get("/password/token-status")
def reset_token_status(token: str = Query(min_length=20, max_length=200), db: Session = Depends(get_db),
                       now: datetime = Depends(now_dep)):
    return {"status": reset.token_status(db, token, now)}


@router.post("/password/reset")
def reset_password(body: ResetIn, db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    result = reset.reset_password(db, body.token, body.password, now)
    if result != "ok":
        raise AppError(400, f"reset_{result}", RESET_MESSAGES[result])
    # No automatic sign-in: the person signs in again with the new password.
    return {"status": "ok"}


@router.get("/google/start")
def google_start(db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    if not google.enabled():
        raise not_found()
    try:
        url = google.start(db, now, "login")
    except google.GoogleError:
        raise not_found() from None
    return RedirectResponse(url, status_code=302)


@router.post("/google/link/start")
def google_link_start(body: PasswordIn, request: Request, actor: Actor = Depends(get_active_actor),
                      db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    """Linking Google to an existing account requires the password again (re-authentication)."""
    if not google.enabled():
        raise not_found()
    user = db.get(User, actor.user_id)
    ip = request.client.host if request.client else "?"
    if rate_limiter.blocked(user.email, ip):
        raise AppError(429, "too_many_attempts", "יותר מדי ניסיונות. נסו שוב בעוד כמה דקות")
    if not verify_password(user.password_hash, body.password):
        rate_limiter.fail(user.email, ip)
        raise AppError(401, "bad_credentials", "הסיסמה שגויה")
    rate_limiter.reset(user.email, ip)
    try:
        url = google.start(db, now, "link", user_id=user.id)
    except google.GoogleError:
        raise not_found() from None
    return {"url": url}


@router.get("/google/callback")
def google_callback(code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db),
                    now: datetime = Depends(now_dep)):
    if not google.enabled():
        raise not_found()
    base = get_settings().public_base_url.rstrip("/")

    def back_with(reason: str) -> RedirectResponse:
        return RedirectResponse(f"{base}/login?google=error&reason={reason}", status_code=302)

    if error or not code or not state:
        return back_with("cancelled")
    try:
        outcome = google.complete(db, code, state, now)
    except google.GoogleError as exc:
        return back_with(exc.reason)
    if outcome.purpose == "link":
        return RedirectResponse(f"{base}/profile?google=linked", status_code=302)
    response = RedirectResponse(f"{base}/", status_code=302)
    _issue_session(db, response, outcome.user, now)
    record(db, actor_id=outcome.user.id, action="login_google", object_type="user", object_id=outcome.user.id, now=now)
    db.commit()
    return response
