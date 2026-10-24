import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.access.journal import journal_access
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import not_found
from app.mentoring import service
from app.mentoring.models import MARKER_KEYS

router = APIRouter(prefix="/api", tags=["journal"])


class RideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    trainee_id: uuid.UUID
    shift_id: uuid.UUID
    description: str | None = Field(default=None, max_length=200)


class DescriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=200)
    expected_version: int


class AssessmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markers: dict[str, str | None] = Field(default_factory=dict)
    summary: str | None = Field(default=None, max_length=4000)
    expected_version: int


class SummaryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strengths: str | None = Field(default=None, max_length=4000)
    strengths_none: bool = False
    improvements: str | None = Field(default=None, max_length=4000)
    improvements_none: bool = False
    expected_version: int


class CancelRideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=300)
    duplicate_of_ride_id: uuid.UUID | None = None


class RestoreRideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=300)


class CorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count_delta: int = Field(default=0, ge=-500, le=500)
    last_ride_date: date | None = None
    reason: str = Field(min_length=2, max_length=300)


class ExtensionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mentor_id: uuid.UUID
    shift_id: uuid.UUID
    hours: int = Field(default=12, ge=1, le=72)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=4000)
    shared: bool = False
    shift_id: uuid.UUID | None = None


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str | None = Field(default=None, max_length=4000)
    shared: bool | None = None
    expected_version: int


def _ride_out(ride) -> dict:
    return {"id": str(ride.id), "seq": ride.seq, "trainee_id": str(ride.trainee_id), "shift_id": str(ride.shift_id),
            "description": ride.description, "description_version": ride.description_version,
            "cancelled": ride.cancelled_at is not None}


@router.get("/journal/me")
def my_journal(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
               now: datetime = Depends(now_dep)):
    if actor.level != "trainee" and not service.progress_for(db, [actor.user_id])[actor.user_id]["ride_count"]:
        raise not_found("journal_not_found", "יומן נסיעות קיים לחניכים")
    return service.journal_view(db, actor, actor.user_id, now) | {
        "documentable_shifts": service.my_documentable_shifts(db, actor, now)}


@router.get("/journal/mentor")
def mentor_home(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                now: datetime = Depends(now_dep)):
    return service.mentor_home(db, actor, now)


@router.get("/journal/trainees")
def trainees(weekday: int | None = Query(None, ge=0, le=6), search: str | None = Query(None, max_length=100),
             actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    return service.manager_trainees(db, actor, now, weekday, search)


@router.get("/journal/trainees/{trainee_id}")
def trainee_journal(trainee_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                    now: datetime = Depends(now_dep)):
    return service.journal_view(db, actor, trainee_id, now)


@router.get("/journal/trainees/{trainee_id}/progress")
def trainee_progress(trainee_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                     now: datetime = Depends(now_dep)):
    if not journal_access(db, actor, trainee_id, now).can_read_progress:
        raise not_found()
    return service.progress_for(db, [trainee_id])[trainee_id]


@router.post("/journal/rides")
def create_ride(body: RideIn, response: Response, actor: Actor = Depends(get_active_actor),
                db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    ride, created = service.create_ride(db, actor, body.id, body.trainee_id, body.shift_id, body.description, now)
    response.status_code = 201 if created else 200
    return _ride_out(ride) | {"created": created}


@router.put("/journal/rides/{ride_id}/description")
def put_description(ride_id: uuid.UUID, body: DescriptionIn, actor: Actor = Depends(get_active_actor),
                    db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    return _ride_out(service.update_description(db, actor, ride_id, body.description, body.expected_version, now))


@router.put("/journal/rides/{ride_id}/assessment")
def put_assessment(ride_id: uuid.UUID, body: AssessmentIn, actor: Actor = Depends(get_active_actor),
                   db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    a = service.update_assessment(db, actor, ride_id, body.markers, body.summary, body.expected_version, now)
    return {"markers": {k: a.markers.get(k) for k in MARKER_KEYS}, "summary": a.summary, "version": a.version}


@router.put("/journal/summaries/{trainee_id}/{shift_id}")
def put_summary(trainee_id: uuid.UUID, shift_id: uuid.UUID, body: SummaryIn, actor: Actor = Depends(get_active_actor),
                db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    data = body.model_dump()
    s = service.save_summary(db, actor, trainee_id, shift_id, data, data.pop("expected_version"), now)
    return service._summary_out(s)


@router.post("/journal/rides/{ride_id}/cancel")
def cancel_ride(ride_id: uuid.UUID, body: CancelRideIn, actor: Actor = Depends(get_active_actor),
                db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    ride, changed = service.cancel_ride(db, actor, ride_id, body.reason, body.duplicate_of_ride_id, now)
    return _ride_out(ride) | {"already_cancelled": not changed,
                              "progress": service.progress_for(db, [ride.trainee_id])[ride.trainee_id]}


@router.post("/journal/rides/{ride_id}/restore")
def restore_ride(ride_id: uuid.UUID, body: RestoreRideIn, actor: Actor = Depends(get_active_actor),
                 db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    ride = service.restore_ride(db, actor, ride_id, body.reason, now)
    return _ride_out(ride) | {"progress": service.progress_for(db, [ride.trainee_id])[ride.trainee_id]}


@router.post("/journal/trainees/{trainee_id}/corrections", status_code=201)
def correction(trainee_id: uuid.UUID, body: CorrectionIn, actor: Actor = Depends(get_active_actor),
               db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.add_correction(db, actor, trainee_id, body.count_delta, body.last_ride_date, body.reason, now)
    return service.progress_for(db, [trainee_id])[trainee_id]


@router.get("/journal/trainees/{trainee_id}/extensions")
def extensions(trainee_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
               now: datetime = Depends(now_dep)):
    return service.extensions_for(db, actor, trainee_id, now)


@router.post("/journal/trainees/{trainee_id}/extensions", status_code=201)
def reopen(trainee_id: uuid.UUID, body: ExtensionIn, actor: Actor = Depends(get_active_actor),
           db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    ext = service.reopen_access(db, actor, body.mentor_id, trainee_id, body.shift_id, body.hours, now)
    return {"id": str(ext.id), "ends_at": ext.ends_at}


@router.post("/journal/extensions/{ext_id}/revoke")
def revoke(ext_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
           now: datetime = Depends(now_dep)):
    service.revoke_extension(db, actor, ext_id, now)
    return {"ok": True}


@router.get("/journal/trainees/{trainee_id}/notes")
def trainee_notes(trainee_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                  now: datetime = Depends(now_dep)):
    return service.notes_for_reader(db, actor, trainee_id, now)


@router.get("/notes")
def notes(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    return service.my_notes(db, actor)


@router.post("/notes", status_code=201)
def create_note(body: NoteIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                now: datetime = Depends(now_dep)):
    return service._note_out(service.create_note(db, actor, body.content, body.shared, body.shift_id, now))


@router.put("/notes/{note_id}")
def update_note(note_id: uuid.UUID, body: NoteUpdate, actor: Actor = Depends(get_active_actor),
                db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    return service._note_out(service.update_note(db, actor, note_id, body.content, body.shared,
                                                 body.expected_version, now))


@router.get("/me/notification-status")
def notification_status(actor: Actor = Depends(get_active_actor)):
    """Server side of the 'notifications are off' notice: no phone channel is connected yet."""
    return {"phone_channel_connected": get_settings().push_channel != "none"}
