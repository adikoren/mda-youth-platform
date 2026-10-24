from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.dependencies import current_session, get_actor, now_dep
from app.accounts.models import AuthSession, User
from app.accounts.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    new_token,
    normalize_email,
    rate_limiter,
    token_hash,
    verify_password,
)
from app.audit.service import record
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import AppError, conflict
from app.core.timeutil import WEEKDAY_NAMES_HE

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Format check only (deliverability is not checked; reserved demo domains like .test must work).
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class SignupIn(BaseModel):
    # extra="forbid": a signup payload cannot carry roles, levels, scopes or status.
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    requested_weekday: int | None = Field(default=None, ge=0, le=6)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    password: str = Field(max_length=200)


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)


def actor_out(actor: Actor, user: User | None = None) -> dict:
    return {
        "id": str(actor.user_id),
        "email": actor.email,
        "full_name": actor.full_name,
        "phone": user.phone if user else None,
        "status": actor.status,
        "level": actor.level,
        "home_weekday": actor.home_weekday,
        "home_weekday_name": WEEKDAY_NAMES_HE[actor.home_weekday] if actor.home_weekday is not None else None,
        "is_admin": actor.is_admin,
        "is_youth_manager": actor.is_youth_manager,
        "managed_weekdays": sorted(actor.managed_weekdays),
        "temporary_days": [{"weekday": wd, "ends_at": ends.isoformat()} for wd, ends in sorted(actor.temporary_weekdays.items())],
    }


def _issue_session(db: Session, response: Response, user: User, now: datetime) -> None:
    s = get_settings()
    token = new_token()
    db.add(AuthSession(token_hash=token_hash(token), user_id=user.id, created_at=now, last_activity_at=now))
    max_age = s.session_idle_days * 24 * 3600  # browser keeps the cookie across app closures
    response.set_cookie(s.session_cookie_name, token, max_age=max_age, httponly=True,
                        secure=s.cookie_secure, samesite="lax", path="/")


def ensure_csrf_cookie(request: Request, response: Response) -> str:
    s = get_settings()
    value = request.cookies.get(s.csrf_cookie_name) or new_token()
    response.set_cookie(s.csrf_cookie_name, value, max_age=s.session_idle_days * 24 * 3600,
                        httponly=False, secure=s.cookie_secure, samesite="lax", path="/")
    return value


@router.get("/csrf")
def csrf(request: Request, response: Response) -> dict:
    return {"csrf": ensure_csrf_cookie(request, response)}


@router.post("/signup", status_code=201)
def signup(body: SignupIn, response: Response, db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    user = User(
        email=normalize_email(body.email),
        full_name=body.full_name.strip(),
        phone=(body.phone or "").strip() or None,
        password_hash=hash_password(body.password),
        status="pending",
        requested_weekday=body.requested_weekday,
        created_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict("email_taken", "כתובת המייל כבר רשומה במערכת")
    record(db, actor_id=user.id, action="signup", object_type="user", object_id=user.id, now=now)
    _issue_session(db, response, user, now)
    db.commit()
    return {"status": "pending"}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db),
          now: datetime = Depends(now_dep)):
    email = normalize_email(body.email)
    ip = request.client.host if request.client else "?"
    if rate_limiter.blocked(email, ip):
        raise AppError(429, "too_many_attempts", "יותר מדי ניסיונות. נסו שוב בעוד כמה דקות")
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        rate_limiter.fail(email, ip)
        raise AppError(401, "bad_credentials", "מייל או סיסמה שגויים")
    if user.status == "inactive":
        raise AppError(403, "account_inactive", "החשבון אינו פעיל")
    rate_limiter.reset(email, ip)
    _issue_session(db, response, user, now)
    record(db, actor_id=user.id, action="login", object_type="user", object_id=user.id, now=now)
    db.commit()
    return {"status": user.status}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    sess = current_session(request, db, now)
    if sess is not None:
        sess.revoked_at = now
        db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)):
    return actor_out(actor, db.get(User, actor.user_id))


@router.patch("/me")
def update_me(body: ProfileIn, actor: Actor = Depends(get_actor), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    user = db.get(User, actor.user_id)
    changed = body.model_dump(exclude_unset=True)
    for key, value in changed.items():
        setattr(user, key, value.strip() if isinstance(value, str) else value)
    record(db, actor_id=actor.user_id, action="profile_update", object_type="user", object_id=user.id,
           now=now, meta={"fields": sorted(changed)})
    db.commit()
    return actor_out(actor, user)
