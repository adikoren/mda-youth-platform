import uuid
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import queries
from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.core.db import get_db
from app.core.timeutil import local_today
from app.days.models import ActivityDay
from app.registration import service
from app.scheduling.service import get_day

router = APIRouter(prefix="/api", tags=["registration"])


class ChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shift_id: uuid.UUID
    available: bool
    arrival_time: time | None = None


class RegistrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: list[ChoiceIn] = Field(min_length=1, max_length=3)
    expected_revision: int | None = None


@router.get("/registrations")
def upcoming(days_ahead: int = 14, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
             now: datetime = Depends(now_dep)):
    """Dates the actor may answer, from today forward (eligibility via the shared SQL predicate)."""
    today = local_today(now)
    days = db.scalars(select(ActivityDay).where(queries.registerable_days(actor, now),
                                                ActivityDay.local_date >= today,
                                                ActivityDay.local_date <= today + timedelta(days=min(days_ahead, 60)))
                      .order_by(ActivityDay.local_date))
    return [service.my_registration(db, actor, d, now) for d in days]


@router.get("/registrations/{day_id}")
def get_mine(day_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
             now: datetime = Depends(now_dep)):
    return service.my_registration(db, actor, get_day(db, day_id), now)


@router.put("/registrations/{day_id}")
def put_mine(day_id: uuid.UUID, body: RegistrationIn, actor: Actor = Depends(get_active_actor),
             db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    answers = [c.model_dump() for c in body.choices]
    result = service.save_registration(db, actor, get_day(db, day_id), answers, body.expected_revision, now)
    return result | {"registration": service.my_registration(db, actor, get_day(db, day_id), now)}


@router.get("/invitations")
def invitations(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                now: datetime = Depends(now_dep)):
    """Only active requests. The UI shows a card per item and nothing when the list is empty."""
    return service.active_invitations(db, actor, now)


@router.post("/invitations/{invitation_id}/confirm")
def confirm(invitation_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
            now: datetime = Depends(now_dep)):
    service.confirm_invitation(db, actor, invitation_id, now)
    return {"ok": True}
