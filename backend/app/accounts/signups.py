"""Day Managers approve pending signups for their own weekday only (decision of 2026-10-17).

Scope is enforced here, in both the list and the approve action: a manager sees and approves only applicants whose
stored requested weekday is one of the weekdays they manage right now (permanent scope, or an active temporary
appointment, which load_actor re-reads on every request). The requested weekday is a preference: it becomes the home
day only through this approval, and the client cannot choose another day or any role. Admin keeps the existing
approval route (app/admin/router.py) and may also use this list.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.access.policies import can_approve_for_weekday
from app.accounts import service as accounts
from app.accounts.models import User
from app.core.db import get_db
from app.core.errors import not_found
from app.core.timeutil import WEEKDAY_NAMES_HE

router = APIRouter(prefix="/api/manage/signups", tags=["manage"])


class SignupApproveIn(BaseModel):
    # extra="forbid": a home day or a role cannot be sent. The home day is the applicant's requested weekday.
    model_config = ConfigDict(extra="forbid")
    level: str = Field(pattern="^(trainee|youth|mentor)$")


def _scope_days(actor: Actor) -> list[int] | None:
    """None for Admin (every day); otherwise the weekdays the actor manages right now."""
    return None if actor.is_admin else sorted(actor.managed_weekdays)


@router.get("")
def pending_signups(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                    now: datetime = Depends(now_dep)):
    days = _scope_days(actor)
    if days is not None and not days:
        raise not_found()
    stmt = select(User.id, User.full_name, User.requested_weekday, User.created_at).where(User.status == "pending")
    if days is not None:
        stmt = stmt.where(User.requested_weekday.in_(days))
    return [{"id": str(uid), "full_name": name, "requested_weekday": wd,
             "requested_weekday_name": WEEKDAY_NAMES_HE[wd] if wd is not None else None,
             "created_at": created.isoformat()}
            for uid, name, wd, created in db.execute(stmt.order_by(User.created_at)).all()]


@router.post("/{user_id}/approve")
def approve_signup(user_id: uuid.UUID, body: SignupApproveIn, actor: Actor = Depends(get_active_actor),
                   db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    # Same answer for "not pending", "another day" and "unknown id": nothing about other applicants is revealed.
    if user is None or user.status != "pending" or user.requested_weekday is None \
            or not can_approve_for_weekday(actor, user.requested_weekday):
        raise not_found("signup_not_found", "הבקשה לא נמצאה")
    accounts.approve_user(db, user, body.level, user.requested_weekday, actor.user_id, now)
    db.commit()
    return {"id": str(user.id), "status": user.status, "home_weekday": user.requested_weekday,
            "level": body.level}
