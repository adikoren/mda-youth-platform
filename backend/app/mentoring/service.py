"""Ride journal services. Every function re-checks access with the current server time, so an
offline draft that syncs after its window closed is refused (and kept on the device)."""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.journal import (
    JournalAccess,
    current_level,
    formal_mentors,
    journal_access,
    mentor_grant_ends,
    mentor_grants,
)
from app.accounts.models import DayMembership, User, VolunteerLevel
from app.audit.service import record
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.timeutil import local_dt, local_weekday
from app.days.models import ActivityDay, Shift
from app.mentoring.models import (
    MARKER_KEYS,
    MARKER_VALUES,
    MentorAccessExtension,
    MentoringSummary,
    PersonalNote,
    ProgressCorrection,
    Ride,
    RideAssessment,
    RideContributor,
)
from app.scheduling.models import Assignment

WINDOW_CLOSED = "חלון התיעוד נסגר. הטיוטה נשמרה במכשיר — אפשר לבקש מהאחראי לפתוח גישה מחדש"
RIDE_CANCELLED = "הנסיעה בוטלה על ידי האחראי (למשל ככפולה). הטיוטה נשמרה במכשיר"


def _access_or_404(db: Session, actor: Actor, trainee_id: uuid.UUID, now: datetime) -> JournalAccess:
    acc = journal_access(db, actor, trainee_id, now)
    if not acc.can_read_journal:
        raise not_found("journal_not_found", "היומן לא נמצא")
    return acc


def _shift_and_day(db: Session, shift_id: uuid.UUID) -> tuple[Shift, ActivityDay]:
    row = db.execute(select(Shift, ActivityDay).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                     .where(Shift.id == shift_id)).first()
    if row is None:
        raise not_found("shift_not_found", "המשמרת לא נמצאה")
    return row[0], row[1]


def _published_assigned(db: Session, user_id: uuid.UUID, shift: Shift, day: ActivityDay) -> bool:
    if day.latest_published_version_id is None:
        return False
    return db.scalar(select(Assignment.id).where(Assignment.version_id == day.latest_published_version_id,
                                                 Assignment.shift_id == shift.id,
                                                 Assignment.user_id == user_id)) is not None


def _touch_contributor(db: Session, ride_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> None:
    db.execute(insert(RideContributor).values(ride_id=ride_id, user_id=user_id, first_at=now, last_at=now)
               .on_conflict_do_update(index_elements=["ride_id", "user_id"], set_={"last_at": now}))


# ---------------- progress ----------------

def progress_for(db: Session, trainee_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Ride count and last ride date, derived from server-confirmed rides plus audited corrections.
    count = rides + sum(count_delta). If a correction sets last_ride_date, the latest such
    correction replaces the derived date, and only rides created after it can move it later."""
    out = {t: {"ride_count": 0, "last_ride_date": None} for t in trainee_ids}
    if not trainee_ids:
        return out
    rides = db.execute(select(Ride.trainee_id, Ride.occurred_on, Ride.created_at)
                       .where(Ride.trainee_id.in_(trainee_ids), Ride.cancelled_at.is_(None))).all()
    corrections = db.execute(select(ProgressCorrection).where(ProgressCorrection.trainee_id.in_(trainee_ids))
                             .order_by(ProgressCorrection.created_at)).scalars().all()
    by_t: dict[uuid.UUID, list] = {}
    for t, occurred, created in rides:
        by_t.setdefault(t, []).append((occurred, created))
    for t in trainee_ids:
        rs = by_t.get(t, [])
        cs = [c for c in corrections if c.trainee_id == t]
        count = len(rs) + sum(c.count_delta for c in cs)
        last = max((o for o, _ in rs), default=None)
        override = next((c for c in reversed(cs) if c.last_ride_date is not None), None)
        if override is not None:
            later = [o for o, created in rs if created > override.created_at]
            last = max([override.last_ride_date, *later])
        out[t] = {"ride_count": max(count, 0), "last_ride_date": last.isoformat() if last else None}
    return out


def add_correction(db: Session, actor: Actor, trainee_id: uuid.UUID, count_delta: int, last_ride_date: date | None,
                   reason: str, now: datetime) -> None:
    acc = journal_access(db, actor, trainee_id, now)
    if not acc.can_manage:
        raise not_found("journal_not_found", "היומן לא נמצא") if not acc.can_read_progress else forbidden()
    if count_delta == 0 and last_ride_date is None:
        raise bad_request("empty_correction", "התיקון לא משנה דבר")
    db.add(ProgressCorrection(trainee_id=trainee_id, count_delta=count_delta, last_ride_date=last_ride_date,
                              reason=reason.strip(), created_by_id=actor.user_id, created_at=now))
    record(db, actor_id=actor.user_id, action="progress_correction", object_type="user", object_id=trainee_id,
           now=now, meta={"count_delta": count_delta, "last_ride_date": str(last_ride_date) if last_ride_date else None})
    db.commit()


# ---------------- rides ----------------

def _validate_description(text: str | None) -> str | None:
    text = (text or "").strip()
    if len(text) > 200:
        raise bad_request("description_too_long", "תיאור המקרה ארוך מדי (עד 200 תווים)")
    return text or None


def create_ride(db: Session, actor: Actor, ride_id: uuid.UUID, trainee_id: uuid.UUID, shift_id: uuid.UUID,
                description: str | None, now: datetime) -> tuple[Ride, bool]:
    """Idempotent: re-sending the same id for the same trainee/shift returns the saved ride."""
    acc = journal_access(db, actor, trainee_id, now)
    shift, day = _shift_and_day(db, shift_id)
    # Lock the trainee row: serialises sequence allocation and concurrent creates with one id.
    trainee = db.scalar(select(User).where(User.id == trainee_id).with_for_update())
    if trainee is None:
        raise not_found("journal_not_found", "היומן לא נמצא")
    existing = db.get(Ride, ride_id)
    if existing is not None:
        if existing.trainee_id == trainee_id and existing.shift_id == shift_id and acc.can_read_journal:
            db.rollback()
            return existing, False
        raise conflict("ride_id_conflict", "מזהה הנסיעה כבר בשימוש")

    if acc.is_self:
        if current_level(db, trainee_id) != "trainee":
            raise forbidden("not_trainee", "רק חניכים מתעדים נסיעות")
        if not _published_assigned(db, trainee_id, shift, day):
            raise forbidden("not_assigned", "אפשר לתעד רק משמרת שבה שובצת")
        if now < local_dt(day.local_date, time(0, 0)):
            raise forbidden("too_early", "אפשר לתעד רק ביום המשמרת או אחריו")
        desc = _validate_description(description)
    elif acc.can_mentor_shift(shift_id):
        if not acc.is_admin and not _published_assigned(db, trainee_id, shift, day):
            raise forbidden("not_assigned", "החניך לא שובץ במשמרת זו")
        desc = None  # the case description is the trainee's part
    elif acc.can_read_journal:
        raise forbidden("mentor_window_closed", WINDOW_CLOSED)
    else:
        raise not_found("journal_not_found", "היומן לא נמצא")
    if acc.is_admin and not acc.is_self and current_level(db, trainee_id) != "trainee":
        raise forbidden("not_trainee", "רק לחניכים יש יומן נסיעות")

    seq = (db.scalar(select(func.max(Ride.seq)).where(Ride.trainee_id == trainee_id)) or 0) + 1
    ride = Ride(id=ride_id, trainee_id=trainee_id, shift_id=shift_id, occurred_on=day.local_date, seq=seq,
                description=desc, description_version=1 if desc else 0,
                description_updated_at=now if desc else None, created_by_id=actor.user_id, created_at=now)
    db.add(ride)
    db.flush()
    _touch_contributor(db, ride.id, actor.user_id, now)
    record(db, actor_id=actor.user_id, action="ride_create", object_type="ride", object_id=ride.id, now=now,
           meta={"trainee": str(trainee_id), "seq": seq})
    db.commit()
    return ride, True


def _ride_for(db: Session, actor: Actor, ride_id: uuid.UUID, now: datetime) -> tuple[Ride, JournalAccess]:
    ride = db.scalar(select(Ride).where(Ride.id == ride_id).with_for_update())
    if ride is None:
        raise not_found("ride_not_found", "הנסיעה לא נמצאה")
    acc = journal_access(db, actor, ride.trainee_id, now)
    if not acc.can_read_journal:
        raise not_found("ride_not_found", "הנסיעה לא נמצאה")
    return ride, acc


def _refuse_cancelled(ride: Ride) -> None:
    if ride.cancelled_at is not None:
        raise conflict("ride_cancelled", RIDE_CANCELLED)


def update_description(db: Session, actor: Actor, ride_id: uuid.UUID, description: str | None,
                       expected_version: int, now: datetime) -> Ride:
    ride, acc = _ride_for(db, actor, ride_id, now)
    if not acc.can_edit_description:
        raise forbidden("trainee_part", "את תיאור המקרה ממלא/ת החניך/ה")
    _refuse_cancelled(ride)
    if ride.description_version != expected_version:
        raise conflict("version_conflict", "התיאור עודכן בינתיים. הטקסט שלך נשמר במכשיר",
                       {"current": {"description": ride.description, "version": ride.description_version}})
    ride.description = _validate_description(description)
    ride.description_version += 1
    ride.description_updated_at = now
    _touch_contributor(db, ride.id, actor.user_id, now)
    record(db, actor_id=actor.user_id, action="ride_description", object_type="ride", object_id=ride.id, now=now)
    db.commit()
    return ride


def cancel_ride(db: Session, actor: Actor, ride_id: uuid.UUID, reason: str, duplicate_of: uuid.UUID | None,
                now: datetime) -> tuple[Ride, bool]:
    """Audited cancellation by the trainee's current home-day manager or an admin (same authority
    as progress corrections). The row and its parts are kept; repeating the request changes nothing."""
    ride, acc = _ride_for(db, actor, ride_id, now)
    if not acc.can_manage:
        raise forbidden("not_home_manager", "רק אחראי היום הקבוע של החניך או מנהל המערכת יכולים לבטל נסיעה")
    if ride.cancelled_at is not None:
        db.rollback()
        return ride, False
    reason = (reason or "").strip()
    if len(reason) < 2:
        raise bad_request("reason_required", "יש לכתוב סיבה לביטול")
    if duplicate_of is not None:
        original = db.get(Ride, duplicate_of)
        if original is None or original.trainee_id != ride.trainee_id or original.id == ride.id:
            raise bad_request("bad_duplicate_of", "הנסיעה המקורית לא נמצאה ביומן של החניך")
        if original.cancelled_at is not None:
            raise conflict("duplicate_of_cancelled", "הנסיעה המקורית בוטלה בעצמה")
    ride.cancelled_at, ride.cancelled_by_id, ride.cancel_reason = now, actor.user_id, reason
    ride.duplicate_of_id = duplicate_of
    # The reason stays on the ride row (visible to the trainee and managers); the audit holds references only.
    record(db, actor_id=actor.user_id, action="ride_cancel", object_type="ride", object_id=ride.id, now=now,
           meta={"trainee": str(ride.trainee_id), "seq": ride.seq,
                 "duplicate_of": str(duplicate_of) if duplicate_of else None})
    db.commit()
    return ride, True


def restore_ride(db: Session, actor: Actor, ride_id: uuid.UUID, reason: str, now: datetime) -> Ride:
    ride, acc = _ride_for(db, actor, ride_id, now)
    if not acc.can_manage:
        raise forbidden("not_home_manager", "רק אחראי היום הקבוע של החניך או מנהל המערכת יכולים לשחזר נסיעה")
    if ride.cancelled_at is None:
        db.rollback()
        return ride
    if len((reason or "").strip()) < 2:
        raise bad_request("reason_required", "יש לכתוב סיבה לשחזור")
    previous = {"cancelled_at": ride.cancelled_at.isoformat(), "cancelled_by": str(ride.cancelled_by_id)}
    ride.cancelled_at = ride.cancelled_by_id = ride.cancel_reason = ride.duplicate_of_id = None
    record(db, actor_id=actor.user_id, action="ride_restore", object_type="ride", object_id=ride.id, now=now,
           meta={"trainee": str(ride.trainee_id), "seq": ride.seq, **previous})
    db.commit()
    return ride


def _validate_markers(markers: dict) -> dict:
    clean = {}
    for key, value in (markers or {}).items():
        if key not in MARKER_KEYS:
            raise bad_request("bad_marker", "סימון לא מוכר")
        if value is None:
            continue  # not selected yet
        if value not in MARKER_VALUES:
            raise bad_request("bad_marker_value", "ערך סימון לא חוקי")
        clean[key] = value
    return clean


def update_assessment(db: Session, actor: Actor, ride_id: uuid.UUID, markers: dict, summary: str | None,
                      expected_version: int, now: datetime) -> RideAssessment:
    ride, acc = _ride_for(db, actor, ride_id, now)
    if not acc.can_mentor_shift(ride.shift_id):
        if acc.is_self:
            raise forbidden("mentor_part", "את ההערכה ממלא/ת החונך/ת")
        raise forbidden("mentor_window_closed", WINDOW_CLOSED)
    _refuse_cancelled(ride)
    clean = _validate_markers(markers)
    current = db.get(RideAssessment, ride.id)
    current_version = current.version if current else 0
    if current_version != expected_version:
        raise conflict("version_conflict", "ההערכה עודכנה בינתיים. הטקסט שלך נשמר במכשיר",
                       {"current": _assessment_out(current)})
    if current is None:
        current = RideAssessment(ride_id=ride.id, markers={}, version=0, updated_at=now, updated_by_id=actor.user_id)
        db.add(current)
    current.markers = clean
    current.summary = (summary or "").strip() or None
    current.version += 1
    current.updated_at, current.updated_by_id = now, actor.user_id
    _touch_contributor(db, ride.id, actor.user_id, now)
    record(db, actor_id=actor.user_id, action="ride_assessment", object_type="ride", object_id=ride.id, now=now,
           meta={"selected": sorted(clean)})
    db.commit()
    return current


def _assessment_out(a: RideAssessment | None) -> dict | None:
    if a is None:
        return None
    return {"markers": {k: a.markers.get(k) for k in MARKER_KEYS}, "summary": a.summary, "version": a.version,
            "updated_at": a.updated_at}


# ---------------- shift-level mentoring summary ----------------

def save_summary(db: Session, actor: Actor, trainee_id: uuid.UUID, shift_id: uuid.UUID, data: dict,
                 expected_version: int, now: datetime) -> MentoringSummary:
    acc = _access_or_404(db, actor, trainee_id, now)
    if not acc.can_mentor_shift(shift_id):
        raise forbidden("mentor_window_closed", WINDOW_CLOSED) if not acc.is_self else forbidden(
            "mentor_part", "את סיכום החניכה ממלא/ת החונך/ת")
    fields = {}
    for name in ("strengths", "improvements"):
        text = (data.get(name) or "").strip() or None
        none = bool(data.get(f"{name}_none"))
        if none and text:
            raise bad_request("text_and_none", "אפשר לבחור טקסט או ״אין הערות״, לא את שניהם")
        fields[name], fields[f"{name}_none"] = text, none
    db.execute(insert(MentoringSummary).values(
        id=uuid.uuid4(), trainee_id=trainee_id, shift_id=shift_id, version=0, updated_at=now,
        updated_by_id=actor.user_id, strengths_none=False, improvements_none=False,
    ).on_conflict_do_nothing(index_elements=["trainee_id", "shift_id"]))
    summary = db.scalar(select(MentoringSummary).where(MentoringSummary.trainee_id == trainee_id,
                                                       MentoringSummary.shift_id == shift_id).with_for_update())
    if summary.version != expected_version:
        raise conflict("version_conflict", "סיכום החניכה עודכן בינתיים. הטקסט שלך נשמר במכשיר",
                       {"current": _summary_out(summary)})
    for k, v in fields.items():
        setattr(summary, k, v)
    summary.version += 1
    summary.updated_at, summary.updated_by_id = now, actor.user_id
    record(db, actor_id=actor.user_id, action="mentoring_summary", object_type="user", object_id=trainee_id, now=now,
           meta={"shift": str(shift_id)})
    db.commit()
    return summary


def _summary_out(s: MentoringSummary | None) -> dict | None:
    if s is None or s.version == 0:
        return None
    return {"strengths": s.strengths, "strengths_none": s.strengths_none, "improvements": s.improvements,
            "improvements_none": s.improvements_none, "version": s.version, "updated_at": s.updated_at}


# ---------------- completeness ----------------

@dataclass
class Completeness:
    mentor_required: bool
    trainee_missing: int  # rides without a case description
    mentor_missing_rides: int  # rides whose assessment is incomplete
    summary_missing: bool

    @property
    def trainee_complete(self) -> bool:
        return self.trainee_missing == 0

    @property
    def mentor_complete(self) -> bool:
        return not self.mentor_required or (self.mentor_missing_rides == 0 and not self.summary_missing)

    @property
    def complete(self) -> bool:
        return self.trainee_complete and self.mentor_complete

    def as_dict(self) -> dict:
        return {"mentor_required": self.mentor_required, "trainee_missing": self.trainee_missing,
                "mentor_missing_rides": self.mentor_missing_rides, "summary_missing": self.summary_missing,
                "complete": self.complete}


def assessment_complete(a: RideAssessment | None) -> bool:
    return a is not None and all(a.markers.get(k) in MARKER_VALUES for k in MARKER_KEYS) and bool(a.summary)


def summary_complete(s: MentoringSummary | None) -> bool:
    return s is not None and (bool(s.strengths) or s.strengths_none) and (bool(s.improvements) or s.improvements_none)


def completeness(db: Session, trainee_id: uuid.UUID, shift: Shift) -> Completeness:
    """All applicable required fields. Without a formal mentor in the crew (or on shifts where
    mentoring is not implemented yet), no mentor part is required."""
    mentor_required = bool(formal_mentors(db, trainee_id, shift.id))
    rides = list(db.scalars(select(Ride).where(Ride.trainee_id == trainee_id, Ride.shift_id == shift.id,
                                               Ride.cancelled_at.is_(None))))
    assessments = {a.ride_id: a for a in db.scalars(select(RideAssessment).where(
        RideAssessment.ride_id.in_([r.id for r in rides])))} if rides else {}
    summary = db.scalar(select(MentoringSummary).where(MentoringSummary.trainee_id == trainee_id,
                                                       MentoringSummary.shift_id == shift.id))
    return Completeness(
        mentor_required=mentor_required,
        trainee_missing=sum(1 for r in rides if not r.description),
        mentor_missing_rides=sum(1 for r in rides if not assessment_complete(assessments.get(r.id))) if mentor_required else 0,
        summary_missing=mentor_required and not summary_complete(summary),
    )


# ---------------- journal read model ----------------

def journal_view(db: Session, actor: Actor, trainee_id: uuid.UUID, now: datetime) -> dict:
    acc = _access_or_404(db, actor, trainee_id, now)
    trainee = db.get(User, trainee_id)
    ride_q = select(Ride).where(Ride.trainee_id == trainee_id).order_by(Ride.occurred_on.desc(), Ride.seq.desc())
    if not acc.can_read_all_summaries:
        ride_q = ride_q.where(Ride.cancelled_at.is_(None))  # mentors never see cancelled rides
    rides = list(db.scalars(ride_q))
    seq_of = {r.id: r.seq for r in rides}
    shift_ids = {r.shift_id for r in rides} | set(acc.mentor_shift_ids)
    if acc.can_read_all_summaries:
        # Every published shift the trainee was assigned to (so missing summaries/descriptions show).
        shift_ids |= set(db.scalars(select(Assignment.shift_id).join(Shift, Shift.id == Assignment.shift_id)
                                    .join(ActivityDay, (ActivityDay.id == Shift.activity_day_id)
                                          & (ActivityDay.latest_published_version_id == Assignment.version_id))
                                    .where(Assignment.user_id == trainee_id)))
    shift_rows = {s.id: (s, d) for s, d in db.execute(
        select(Shift, ActivityDay).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
        .where(Shift.id.in_(shift_ids))).all()} if shift_ids else {}
    assessments = {a.ride_id: a for a in db.scalars(select(RideAssessment).where(
        RideAssessment.ride_id.in_([r.id for r in rides])))} if rides else {}
    names = dict(db.execute(select(User.id, User.full_name)).all())
    contributors: dict[uuid.UUID, list[str]] = {}
    for rid, uid in db.execute(select(RideContributor.ride_id, RideContributor.user_id).where(
            RideContributor.ride_id.in_([r.id for r in rides]))).all() if rides else []:
        contributors.setdefault(rid, []).append(names.get(uid, ""))

    # Mentors see historical per-case records but never previous shift-level summaries.
    summary_q = select(MentoringSummary).where(MentoringSummary.trainee_id == trainee_id)
    if not acc.can_read_all_summaries:
        summary_q = summary_q.where(MentoringSummary.shift_id.in_(list(acc.mentor_shift_ids) or [uuid.uuid4()]))
    summaries = {s.shift_id: s for s in db.scalars(summary_q)}

    def shift_info(sid):
        s, d = shift_rows[sid]
        return {"shift_id": str(sid), "kind": s.kind, "date": d.local_date.isoformat(),
                "weekday": local_weekday(d.local_date)}

    ride_out = [{
        "id": str(r.id), "seq": r.seq, "occurred_on": r.occurred_on.isoformat(), **shift_info(r.shift_id),
        "description": r.description, "description_version": r.description_version,
        "awaiting_description": r.description is None,
        "assessment": _assessment_out(assessments.get(r.id)),
        "assessment_complete": assessment_complete(assessments.get(r.id)),
        "contributors": sorted(contributors.get(r.id, [])),
        "can_assess": acc.can_mentor_shift(r.shift_id) and r.cancelled_at is None,
        "can_edit_description": acc.can_edit_description and r.cancelled_at is None,
        "cancelled": None if r.cancelled_at is None else {
            "at": r.cancelled_at, "reason": r.cancel_reason,
            "duplicate_of_seq": seq_of.get(r.duplicate_of_id) if r.duplicate_of_id else None},
    } for r in rides]

    shifts_out = []
    visible_shift_ids = (shift_ids if acc.can_read_all_summaries else set(acc.mentor_shift_ids))
    for sid in sorted(visible_shift_ids, key=lambda x: shift_rows[x][1].local_date, reverse=True):
        s, d = shift_rows[sid]
        comp = completeness(db, trainee_id, s)
        mentors = formal_mentors(db, trainee_id, sid)
        shifts_out.append({**shift_info(sid), "summary": _summary_out(summaries.get(sid)),
                           "can_mentor": acc.can_mentor_shift(sid), "mentors": sorted(({"id": str(m), "full_name": names[m]} for m in mentors),
                                                      key=lambda x: x["full_name"]),
                           "completeness": comp.as_dict()})
    record_read = acc.is_admin and not (acc.is_self or acc.is_home_manager or acc.mentor_shift_ids)
    if record_read:
        record(db, actor_id=actor.user_id, action="admin_journal_read", object_type="user", object_id=trainee_id,
               now=now, admin_only=True)
        db.commit()
    return {
        "trainee": {"id": str(trainee_id), "full_name": trainee.full_name},
        "progress": progress_for(db, [trainee_id])[trainee_id],
        "rides": ride_out,
        "shifts": shifts_out,
        "access": {"is_self": acc.is_self, "is_admin": acc.is_admin, "is_home_manager": acc.is_home_manager,
                   "mentor_shift_ids": [str(s) for s in acc.mentor_shift_ids], "can_manage": acc.can_manage,
                   # When the actor's current mentor access to each shift ends (offline copy limit).
                   "mentor_access_until": {str(sid): end for sid, end in
                                           mentor_grant_ends(db, actor, now).get(trainee_id, {}).items()}},
    }


def mentor_home(db: Session, actor: Actor, now: datetime) -> list[dict]:
    grants = mentor_grants(db, actor, now)
    if not grants:
        return []
    names = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(list(grants)))).all())
    out = []
    for trainee_id, shift_ids in grants.items():
        for sid in shift_ids:
            s, d = _shift_and_day(db, sid)
            out.append({"trainee_id": str(trainee_id), "full_name": names.get(trainee_id), "shift_id": str(sid),
                        "kind": s.kind, "date": d.local_date.isoformat(), "weekday": local_weekday(d.local_date),
                        "completeness": completeness(db, trainee_id, s).as_dict()})
    return sorted(out, key=lambda x: x["full_name"] or "")


def my_documentable_shifts(db: Session, actor: Actor, now: datetime) -> list[dict]:
    """Published shifts the trainee was assigned to, recent first (for "new ride")."""
    rows = db.execute(select(Shift, ActivityDay).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                      .join(Assignment, (Assignment.shift_id == Shift.id)
                            & (Assignment.version_id == ActivityDay.latest_published_version_id))
                      .where(Assignment.user_id == actor.user_id)
                      .order_by(ActivityDay.local_date.desc()).limit(10)).all()
    return [{"shift_id": str(s.id), "kind": s.kind, "date": d.local_date.isoformat(),
             "weekday": local_weekday(d.local_date)}
            for s, d in rows if local_dt(d.local_date, time(0, 0)) <= now]


# ---------------- mentor access extensions ----------------

def reopen_access(db: Session, actor: Actor, mentor_id: uuid.UUID, trainee_id: uuid.UUID, shift_id: uuid.UUID,
                  hours: int, now: datetime) -> MentorAccessExtension:
    acc = journal_access(db, actor, trainee_id, now)
    if not acc.can_manage:
        raise not_found("journal_not_found", "היומן לא נמצא") if not acc.can_read_progress else forbidden(
            "not_home_manager", "רק אחראי היום הקבוע של החניך יכול לפתוח גישה מחדש")
    if current_level(db, mentor_id) != "mentor":
        raise conflict("not_mentor", "אפשר לפתוח גישה רק לחונך")
    _shift_and_day(db, shift_id)
    ext = MentorAccessExtension(mentor_id=mentor_id, trainee_id=trainee_id, shift_id=shift_id,
                                granted_by_id=actor.user_id, starts_at=now, ends_at=now + timedelta(hours=hours))
    db.add(ext)
    record(db, actor_id=actor.user_id, action="mentor_access_reopen", object_type="user", object_id=trainee_id,
           now=now, meta={"mentor": str(mentor_id), "shift": str(shift_id), "hours": hours})
    db.commit()
    return ext


def revoke_extension(db: Session, actor: Actor, ext_id: uuid.UUID, now: datetime) -> None:
    ext = db.get(MentorAccessExtension, ext_id)
    if ext is None:
        raise not_found()
    if not journal_access(db, actor, ext.trainee_id, now).can_manage:
        raise not_found()
    if ext.revoked_at is None:
        ext.revoked_at, ext.revoked_by_id = now, actor.user_id
        record(db, actor_id=actor.user_id, action="mentor_access_revoke", object_type="user",
               object_id=ext.trainee_id, now=now)
    db.commit()


def extensions_for(db: Session, actor: Actor, trainee_id: uuid.UUID, now: datetime) -> list[dict]:
    if not journal_access(db, actor, trainee_id, now).can_manage:
        raise not_found()
    names = dict(db.execute(select(User.id, User.full_name)).all())
    return [{"id": str(e.id), "mentor": names.get(e.mentor_id), "shift_id": str(e.shift_id),
             "ends_at": e.ends_at, "active": e.revoked_at is None and e.ends_at > now}
            for e in db.scalars(select(MentorAccessExtension).where(MentorAccessExtension.trainee_id == trainee_id)
                                .order_by(MentorAccessExtension.starts_at.desc()))]


# ---------------- personal notes ----------------

def _note_out(n: PersonalNote) -> dict:
    return {"id": str(n.id), "content": n.content, "shared": n.shared, "version": n.version,
            "shift_id": str(n.shift_id) if n.shift_id else None, "created_at": n.created_at, "updated_at": n.updated_at}


def my_notes(db: Session, actor: Actor) -> list[dict]:
    return [_note_out(n) for n in db.scalars(select(PersonalNote).where(PersonalNote.author_id == actor.user_id)
                                             .order_by(PersonalNote.created_at.desc()))]


def create_note(db: Session, actor: Actor, content: str, shared: bool, shift_id: uuid.UUID | None,
                now: datetime) -> PersonalNote:
    if actor.level != "trainee":
        raise forbidden("not_trainee", "סיכומים אישיים זמינים לחניכים")
    content = (content or "").strip()
    if not content:
        raise bad_request("empty_note", "הסיכום ריק")
    note = PersonalNote(author_id=actor.user_id, shift_id=shift_id, content=content, shared=shared, version=1,
                        created_at=now, updated_at=now)
    db.add(note)
    db.flush()
    # admin_only: a manager-facing feed must never reveal that a private note exists.
    record(db, actor_id=actor.user_id, action="note_create", object_type="personal_note", object_id=note.id,
           now=now, admin_only=True, meta={"shared": shared})
    db.commit()
    return note


def update_note(db: Session, actor: Actor, note_id: uuid.UUID, content: str | None, shared: bool | None,
                expected_version: int, now: datetime) -> PersonalNote:
    note = db.scalar(select(PersonalNote).where(PersonalNote.id == note_id).with_for_update())
    if note is None or (note.author_id != actor.user_id and not actor.is_admin):
        raise not_found("note_not_found", "הסיכום לא נמצא")
    if note.version != expected_version:
        raise conflict("version_conflict", "הסיכום עודכן ממכשיר אחר. הטקסט שלך נשמר במכשיר",
                       {"current": _note_out(note)})
    if content is not None:
        if not content.strip():
            raise bad_request("empty_note", "הסיכום ריק")
        note.content = content.strip()
    if shared is not None:
        note.shared = shared
    note.version += 1
    note.updated_at = now
    record(db, actor_id=actor.user_id, action="note_update", object_type="personal_note", object_id=note.id,
           now=now, admin_only=True, meta={"shared": note.shared})
    db.commit()
    return note


def notes_for_reader(db: Session, actor: Actor, trainee_id: uuid.UUID, now: datetime) -> list[dict]:
    """Admin: all notes (audited). Current home-day manager: shared notes only, no hint of others.
    Everyone else: 404."""
    acc = journal_access(db, actor, trainee_id, now)
    q = select(PersonalNote).where(PersonalNote.author_id == trainee_id).order_by(PersonalNote.created_at.desc())
    if acc.is_admin:
        record(db, actor_id=actor.user_id, action="admin_notes_read", object_type="user", object_id=trainee_id,
               now=now, admin_only=True)
        db.commit()
        return [_note_out(n) for n in db.scalars(q)]
    if acc.is_home_manager:
        return [_note_out(n) for n in db.scalars(q.where(PersonalNote.shared.is_(True)))]
    raise not_found()


def manager_trainees(db: Session, actor: Actor, now: datetime) -> list[dict]:
    """Home-day trainees of the actor's managed weekdays (journal access) or, for admin/youth
    manager, all trainees (progress; journal only for admin)."""
    q = (select(User.id, User.full_name, DayMembership.weekday)
         .join(VolunteerLevel, (VolunteerLevel.user_id == User.id) & VolunteerLevel.valid_to.is_(None)
               & (VolunteerLevel.level == "trainee"))
         .outerjoin(DayMembership, (DayMembership.user_id == User.id) & DayMembership.valid_to.is_(None))
         .where(User.status == "active").order_by(User.full_name))
    if not (actor.is_admin or actor.is_youth_manager):
        if not actor.managed_weekdays:
            raise forbidden()
        q = q.where(DayMembership.weekday.in_(sorted(actor.managed_weekdays)))
    rows = db.execute(q).all()
    prog = progress_for(db, [r[0] for r in rows])
    return [{"id": str(uid), "full_name": name, "home_weekday": wd, **prog[uid],
             "journal_access": actor.is_admin or (wd is not None and wd in actor.managed_weekdays)}
            for uid, name, wd in rows]

