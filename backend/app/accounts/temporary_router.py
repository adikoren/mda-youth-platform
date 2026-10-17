"""Temporary Day Manager appointments API. Admin and Youth Manager only; every request re-checks the caller."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.accounts import temporary as svc
from app.accounts.models import TemporaryDayManagerAppointment
from app.core.db import get_db
from app.core.errors import forbidden
from app.core.timeutil import WEEKDAY_NAMES_HE
from app.scheduling.pool import people

router = APIRouter(prefix="/api/day-appointments", tags=["day-appointments"])


def _manage_only(actor: Actor) -> None:
    if not (actor.is_admin or actor.is_youth_manager):
        raise forbidden("not_allowed", "רק אדמין או אחראי נוער יכולים לנהל מינויים זמניים")


class AppointmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID | None = None  # required on create, not used on edit
    weekday: int = Field(ge=0, le=6)
    starts: str = Field(min_length=16, max_length=16)  # YYYY-MM-DDTHH:MM, Asia/Jerusalem
    ends: str = Field(min_length=16, max_length=16)


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool


def _local(dt: datetime) -> str:
    return dt.astimezone(svc.tz()).strftime("%Y-%m-%dT%H:%M")


def _out(db: Session, appt: TemporaryDayManagerAppointment, now: datetime) -> dict:
    info = people(db, {appt.user_id, appt.created_by_id} | ({appt.updated_by_id} if appt.updated_by_id else set())
                  | ({appt.revoked_by_id} if appt.revoked_by_id else set()))
    person = info.get(appt.user_id)
    return {
        "id": str(appt.id), "user_id": str(appt.user_id), "full_name": person.full_name if person else "",
        "level": person.level if person else None,
        "weekday": appt.weekday, "weekday_name": WEEKDAY_NAMES_HE[appt.weekday],
        "starts": _local(appt.starts_at), "ends": _local(appt.ends_at),
        "status": svc.status_of(appt, now),
        "created_by": info[appt.created_by_id].full_name if appt.created_by_id in info else "",
        "created_at": appt.created_at.isoformat(),
        "updated_by": info[appt.updated_by_id].full_name if appt.updated_by_id in info else None,
        "updated_at": appt.updated_at.isoformat() if appt.updated_at else None,
        "revoked_by": info[appt.revoked_by_id].full_name if appt.revoked_by_id in info else None,
        "revoked_at": appt.revoked_at.isoformat() if appt.revoked_at else None,
    }


@router.get("/temporary")
def list_temporary(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                   now: datetime = Depends(now_dep)):
    _manage_only(actor)
    rows = list(db.scalars(select(TemporaryDayManagerAppointment).order_by(
        TemporaryDayManagerAppointment.weekday, TemporaryDayManagerAppointment.starts_at.desc())))
    return [_out(db, r, now) for r in rows]


@router.post("/temporary", status_code=201)
def create_temporary(body: AppointmentIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                     now: datetime = Depends(now_dep)):
    _manage_only(actor)
    if body.user_id is None:
        from app.core.errors import bad_request
        raise bad_request("user_required", "יש לבחור מתנדב/ת")
    appt = svc.create(db, actor.user_id, body.user_id, body.weekday, svc.parse_local(body.starts),
                      svc.parse_local(body.ends), now)
    return _out(db, appt, now)


@router.put("/temporary/{appt_id}")
def edit_temporary(appt_id: uuid.UUID, body: AppointmentIn, actor: Actor = Depends(get_active_actor),
                   db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    _manage_only(actor)
    appt = svc.edit(db, actor.user_id, appt_id, body.weekday, svc.parse_local(body.starts), svc.parse_local(body.ends), now)
    return _out(db, appt, now)


@router.post("/temporary/{appt_id}/revoke")
def revoke_temporary(appt_id: uuid.UUID, body: RevokeIn, actor: Actor = Depends(get_active_actor),
                     db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    _manage_only(actor)
    if not body.confirm:
        from app.core.errors import bad_request
        raise bad_request("confirm_required", "יש לאשר את הביטול")
    appt = svc.revoke(db, actor.user_id, appt_id, now)
    return _out(db, appt, now)
