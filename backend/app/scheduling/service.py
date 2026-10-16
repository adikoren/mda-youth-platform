"""Draft editing and publication.

Concurrency model: every draft mutation and every publication first locks the activity_days row
(SELECT ... FOR UPDATE). That serialises edits of one date across all its slots and shifts, so
cross-slot rules (same person twice in a shift, trainee reinforcement+evening) are checked against
a stable state. The unique constraint uq_assignment_person_shift is the database safety net.
Capacity is informational only (soft warning)."""
import uuid
from datetime import datetime, time

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.policies import require_manage_day
from app.accounts.models import User
from app.accounts.service import current_home_weekday, current_level
from app.audit.service import record
from app.core.errors import bad_request, conflict, not_found
from app.core.timeutil import SATURDAY, local_midnight_after, local_weekday
from app.days.models import ActivityDay, Shift
from app.notifications.outbox import enqueue
from app.registration.models import (
    AvailabilityAttention,
    Invitation,
    ManagerAddedParticipant,
    QueueOrder,
)
from app.scheduling.models import AmbulanceSlot, Assignment, BoardingEntry, ScheduleVersion
from app.scheduling.liveness import live_assignment, live_boarding
from app.scheduling.pool import assigned_by_shift, shift_pools

DEFAULT_CAPACITY = 3
# Upper bound for the capacity SETTING (decision 2026-10-12). It does not limit the crew: assigning
# more people than the capacity still succeeds and only shows a warning. Older slots above the limit
# are never changed automatically; they are flagged on the board and listed by `cli report-capacity`.
MAX_CAPACITY = 3


def _check_capacity(capacity: int) -> None:
    if not 0 <= capacity <= MAX_CAPACITY:
        raise bad_request("capacity_out_of_range", f"מספר המקומות באמבולנס יכול להיות בין 0 ל־{MAX_CAPACITY}")


def slots_over_capacity_limit(db: Session) -> list[dict]:
    """Every slot (any version) whose capacity is above MAX_CAPACITY, for manual review."""
    rows = db.execute(
        select(ActivityDay.local_date, Shift.kind, AmbulanceSlot.ordinal, AmbulanceSlot.capacity,
               ScheduleVersion.status, ScheduleVersion.revision, AmbulanceSlot.slot_key)
        .join(ScheduleVersion, ScheduleVersion.id == AmbulanceSlot.version_id)
        .join(Shift, Shift.id == AmbulanceSlot.shift_id)
        .join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
        .where(AmbulanceSlot.capacity > MAX_CAPACITY)
        .order_by(ActivityDay.local_date, Shift.kind, AmbulanceSlot.ordinal, ScheduleVersion.revision)).all()
    return [{"date": d.isoformat(), "shift": k, "ambulance": o, "capacity": c, "version_status": st,
             "revision": rev, "slot_key": str(key)} for d, k, o, c, st, rev, key in rows]


# ---------------- loading & locking ----------------

def lock_day(db: Session, day_id: uuid.UUID) -> ActivityDay:
    day = db.scalar(select(ActivityDay).where(ActivityDay.id == day_id).with_for_update())
    if day is None:
        raise not_found("day_not_found", "היום לא נמצא")
    return day


def get_day(db: Session, day_id: uuid.UUID) -> ActivityDay:
    day = db.get(ActivityDay, day_id)
    if day is None:
        raise not_found("day_not_found", "היום לא נמצא")
    return day


def day_shifts(db: Session, day_id: uuid.UUID) -> list[Shift]:
    order = {"morning": 0, "reinforcement": 1, "evening": 2}
    return sorted(db.scalars(select(Shift).where(Shift.activity_day_id == day_id)), key=lambda s: order[s.kind])


def current_draft(db: Session, day_id: uuid.UUID, for_update: bool = False) -> ScheduleVersion | None:
    q = select(ScheduleVersion).where(ScheduleVersion.activity_day_id == day_id, ScheduleVersion.status == "draft")
    if for_update:
        q = q.with_for_update()
    return db.scalar(q)


def _ensure_draft_locked(db: Session, day: ActivityDay, actor_id: uuid.UUID | None, now: datetime) -> ScheduleVersion:
    """Caller holds the day lock. Creates the working draft as a copy of the last publication
    (or from baseline slot counts the first time)."""
    draft = current_draft(db, day.id)
    if draft is not None:
        return draft
    max_rev = db.scalar(select(func.max(ScheduleVersion.revision)).where(ScheduleVersion.activity_day_id == day.id))
    base_id = day.latest_published_version_id
    draft = ScheduleVersion(activity_day_id=day.id, revision=(max_rev or 0) + 1, status="draft",
                            base_published_version_id=base_id, created_at=now, created_by_id=actor_id, edit_count=0)
    db.add(draft)
    db.flush()
    if base_id is None:
        for shift in day_shifts(db, day.id):
            for i in range(shift.baseline_slots):
                db.add(AmbulanceSlot(version_id=draft.id, shift_id=shift.id, ordinal=i + 1, capacity=DEFAULT_CAPACITY))
    else:
        slot_map: dict[uuid.UUID, uuid.UUID] = {}
        for s in db.scalars(select(AmbulanceSlot).where(AmbulanceSlot.version_id == base_id)):
            new = AmbulanceSlot(id=uuid.uuid4(), version_id=draft.id, shift_id=s.shift_id, slot_key=s.slot_key,
                                ordinal=s.ordinal, capacity=s.capacity, status=s.status,
                                operator_user_id=s.operator_user_id, operator_start=s.operator_start,
                                operator_end=s.operator_end)
            slot_map[s.id] = new.id
            db.add(new)
        db.flush()
        # A volunteer who cancelled on the base version is not carried into the new working copy.
        for a in db.scalars(select(Assignment).where(Assignment.version_id == base_id, live_assignment())):
            db.add(Assignment(version_id=draft.id, shift_id=a.shift_id, slot_id=slot_map[a.slot_id],
                              user_id=a.user_id, created_at=a.created_at, created_by_id=a.created_by_id))
        # A shift type added after the last publication has no ambulances in the base: give it its baseline here.
        for b in db.scalars(select(BoardingEntry).where(BoardingEntry.version_id == base_id, live_boarding(BoardingEntry))):
            db.add(BoardingEntry(version_id=draft.id, shift_id=b.shift_id, user_id=b.user_id, position=b.position))
        in_base = {s.shift_id for s in db.scalars(select(AmbulanceSlot).where(AmbulanceSlot.version_id == base_id))}
        for shift in day_shifts(db, day.id):
            if shift.id not in in_base:
                _add_baseline_slots(db, draft.id, shift)
    db.flush()
    return draft


def ensure_draft(db: Session, actor: Actor, day_id: uuid.UUID, now: datetime) -> ScheduleVersion:
    day = lock_day(db, day_id)
    require_manage_day(actor, day)
    draft = _ensure_draft_locked(db, day, actor.user_id, now)
    db.commit()
    return draft


def _begin_edit(db: Session, actor: Actor, day_id: uuid.UUID, now: datetime) -> tuple[ActivityDay, ScheduleVersion]:
    day = lock_day(db, day_id)
    require_manage_day(actor, day)
    draft = _ensure_draft_locked(db, day, actor.user_id, now)
    draft.edit_count += 1
    draft.last_edit_at = now
    draft.last_edit_by_id = actor.user_id
    return day, draft


def _draft_slot(db: Session, draft: ScheduleVersion, slot_key: uuid.UUID) -> AmbulanceSlot:
    slot = db.scalar(select(AmbulanceSlot).where(AmbulanceSlot.version_id == draft.id,
                                                 AmbulanceSlot.slot_key == slot_key))
    if slot is None:
        raise not_found("slot_not_found", "האמבולנס לא נמצא בטיוטה")
    return slot


def _slot_day_id(db: Session, slot_key: uuid.UUID) -> uuid.UUID:
    day_id = db.scalar(select(Shift.activity_day_id).join(AmbulanceSlot, AmbulanceSlot.shift_id == Shift.id)
                       .where(AmbulanceSlot.slot_key == slot_key).limit(1))
    if day_id is None:
        raise not_found("slot_not_found", "האמבולנס לא נמצא")
    return day_id


def _shift(db: Session, shift_id: uuid.UUID) -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise not_found("shift_not_found", "המשמרת לא נמצאה")
    return shift


# ---------------- slot operations (draft only) ----------------
# The API addresses slots by slot_key (stable across versions), so the client never holds ids of
# published rows that it could try to edit.

def add_slot(db: Session, actor: Actor, shift_id: uuid.UUID, capacity: int, now: datetime) -> AmbulanceSlot:
    _check_capacity(capacity)
    shift = _shift(db, shift_id)
    _, draft = _begin_edit(db, actor, shift.activity_day_id, now)
    ordinal = (db.scalar(select(func.max(AmbulanceSlot.ordinal)).where(
        AmbulanceSlot.version_id == draft.id, AmbulanceSlot.shift_id == shift.id)) or 0) + 1
    slot = AmbulanceSlot(version_id=draft.id, shift_id=shift.id, ordinal=ordinal, capacity=capacity)
    db.add(slot)
    record(db, actor_id=actor.user_id, action="draft_slot_add", object_type="schedule_version",
           object_id=draft.id, now=now, meta={"shift": shift.kind, "capacity": capacity})
    db.commit()
    return slot


def set_slot_capacity(db: Session, actor: Actor, slot_key: uuid.UUID, capacity: int, now: datetime) -> None:
    _check_capacity(capacity)
    _, draft = _begin_edit(db, actor, _slot_day_id(db, slot_key), now)
    slot = _draft_slot(db, draft, slot_key)
    slot.capacity = capacity
    record(db, actor_id=actor.user_id, action="draft_slot_capacity", object_type="schedule_version",
           object_id=draft.id, now=now, meta={"slot_key": str(slot_key), "capacity": capacity})
    db.commit()


def cancel_slot(db: Session, actor: Actor, slot_key: uuid.UUID, now: datetime) -> list[uuid.UUID]:
    """Draft-only cancellation. Live assignments and access are untouched until publication."""
    _, draft = _begin_edit(db, actor, _slot_day_id(db, slot_key), now)
    slot = _draft_slot(db, draft, slot_key)
    if slot.status == "cancelled":
        db.rollback()
        return []
    users = list(db.scalars(select(Assignment.user_id).where(Assignment.slot_id == slot.id)))
    db.execute(delete(Assignment).where(Assignment.slot_id == slot.id))
    slot.status = "cancelled"
    record(db, actor_id=actor.user_id, action="draft_slot_cancel", object_type="schedule_version",
           object_id=draft.id, now=now, meta={"slot_key": str(slot_key), "released": [str(u) for u in users]})
    db.commit()
    return users


def restore_slot(db: Session, actor: Actor, slot_key: uuid.UUID, now: datetime) -> None:
    _, draft = _begin_edit(db, actor, _slot_day_id(db, slot_key), now)
    slot = _draft_slot(db, draft, slot_key)
    slot.status = "active"
    record(db, actor_id=actor.user_id, action="draft_slot_restore", object_type="schedule_version",
           object_id=draft.id, now=now, meta={"slot_key": str(slot_key)})
    db.commit()


# ---------------- assignment operations (draft only) ----------------

def _check_assignable_user(db: Session, user_id: uuid.UUID) -> str:
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        raise not_found("user_not_found", "המתנדב לא נמצא או אינו פעיל")
    level = current_level(db, user_id)
    if level is None:
        raise conflict("no_level", "למתנדב לא הוגדרה רמה")
    return level


def _check_trainee_conflict(db: Session, draft: ScheduleVersion, shift: Shift, user_id: uuid.UUID, level: str) -> None:
    """HARD BLOCK: a trainee may not be in both reinforcement and evening of one date/version."""
    if level != "trainee" or shift.kind not in ("reinforcement", "evening"):
        return
    other_kind = "evening" if shift.kind == "reinforcement" else "reinforcement"
    clash = db.scalar(select(Assignment.id).join(Shift, Shift.id == Assignment.shift_id).where(
        Assignment.version_id == draft.id, Assignment.user_id == user_id, Shift.kind == other_kind,
        Shift.activity_day_id == shift.activity_day_id))
    if clash is not None:
        raise conflict("trainee_reinforcement_evening", "חניך לא יכול להיות משובץ גם בתגבור וגם בערב באותו יום")


def _ensure_in_pool(db: Session, actor: Actor, day: ActivityDay, shift: Shift, user_id: uuid.UUID, now: datetime) -> None:
    pools = shift_pools(db, day, day_shifts(db, day.id))
    if user_id not in pools.get(shift.id, {}):
        _add_participant_locked(db, actor, day, shift, user_id, now)


def assign(db: Session, actor: Actor, slot_key: uuid.UUID, user_id: uuid.UUID, now: datetime) -> Assignment:
    day, draft = _begin_edit(db, actor, _slot_day_id(db, slot_key), now)
    slot = _draft_slot(db, draft, slot_key)
    if slot.status != "active":
        raise conflict("slot_cancelled", "האמבולנס מבוטל בטיוטה")
    shift = _shift(db, slot.shift_id)
    level = _check_assignable_user(db, user_id)
    existing = db.scalar(select(Assignment).where(Assignment.version_id == draft.id, Assignment.shift_id == shift.id,
                                                  Assignment.user_id == user_id))
    if existing is not None:
        raise conflict("already_in_shift", "המתנדב כבר משובץ באמבולנס אחר במשמרת זו. אפשר להעביר אותו",
                       {"assignment_id": str(existing.id)})
    _check_trainee_conflict(db, draft, shift, user_id, level)
    _ensure_in_pool(db, actor, day, shift, user_id, now)
    a = Assignment(version_id=draft.id, shift_id=shift.id, slot_id=slot.id, user_id=user_id, created_at=now,
                   created_by_id=actor.user_id)
    db.add(a)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict("already_in_shift", "המתנדב כבר משובץ במשמרת זו")
    record(db, actor_id=actor.user_id, action="draft_assign", object_type="schedule_version", object_id=draft.id,
           now=now, meta={"user": str(user_id), "slot_key": str(slot_key)})
    db.commit()
    return a


def _draft_assignment(db: Session, draft: ScheduleVersion, user_id: uuid.UUID, shift_id: uuid.UUID) -> Assignment:
    a = db.scalar(select(Assignment).where(Assignment.version_id == draft.id, Assignment.user_id == user_id,
                                           Assignment.shift_id == shift_id))
    if a is None:
        raise not_found("assignment_not_found", "השיבוץ לא נמצא בטיוטה")
    return a


def move(db: Session, actor: Actor, shift_id: uuid.UUID, user_id: uuid.UUID, to_slot_key: uuid.UUID, now: datetime) -> None:
    """Move within the same shift: replaces the person's draft assignment (never duplicates it)."""
    shift = _shift(db, shift_id)
    _, draft = _begin_edit(db, actor, shift.activity_day_id, now)
    a = _draft_assignment(db, draft, user_id, shift_id)
    target = _draft_slot(db, draft, to_slot_key)
    if target.shift_id != shift_id:
        raise bad_request("other_shift", "אפשר להעביר רק בתוך אותה משמרת")
    if target.status != "active":
        raise conflict("slot_cancelled", "האמבולנס מבוטל בטיוטה")
    a.slot_id = target.id
    record(db, actor_id=actor.user_id, action="draft_move", object_type="schedule_version", object_id=draft.id,
           now=now, meta={"user": str(user_id), "to_slot_key": str(to_slot_key)})
    db.commit()


def unassign(db: Session, actor: Actor, shift_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> None:
    shift = _shift(db, shift_id)
    _, draft = _begin_edit(db, actor, shift.activity_day_id, now)
    a = _draft_assignment(db, draft, user_id, shift_id)
    db.delete(a)
    record(db, actor_id=actor.user_id, action="draft_unassign", object_type="schedule_version", object_id=draft.id,
           now=now, meta={"user": str(user_id), "shift": shift.kind})
    db.commit()


# ---------------- pool: manager-added participants, invitations, order ----------------

def _is_guest(db: Session, day: ActivityDay, user_id: uuid.UUID) -> bool:
    wd = local_weekday(day.local_date)
    return wd != SATURDAY and current_home_weekday(db, user_id) != wd


def _add_participant_locked(db: Session, actor: Actor, day: ActivityDay, shift: Shift, user_id: uuid.UUID,
                            now: datetime) -> bool:
    _check_assignable_user(db, user_id)
    exists_row = db.scalar(select(ManagerAddedParticipant.id).where(
        ManagerAddedParticipant.shift_id == shift.id, ManagerAddedParticipant.user_id == user_id,
        ManagerAddedParticipant.removed_at.is_(None)))
    if exists_row is not None:
        return False
    db.add(ManagerAddedParticipant(shift_id=shift.id, user_id=user_id, added_by_id=actor.user_id, added_at=now))
    record(db, actor_id=actor.user_id, action="participant_add", object_type="shift", object_id=shift.id, now=now,
           meta={"user": str(user_id)})
    if _is_guest(db, day, user_id):
        # Direct guest addition: confirmation requested, preferred but not required.
        has = db.scalar(select(Invitation.id).where(Invitation.user_id == user_id, Invitation.activity_day_id == day.id,
                                                    Invitation.kind == "direct_add",
                                                    Invitation.status.in_(("pending", "confirmed"))))
        if has is None:
            inv = Invitation(user_id=user_id, activity_day_id=day.id, shift_id=shift.id, invited_by_id=actor.user_id,
                             kind="direct_add", status="pending", created_at=now,
                             expires_at=local_midnight_after(day.local_date))
            db.add(inv)
            db.flush()
            enqueue(db, event_type="direct_add_confirm", recipients=[user_id], dedup_prefix=f"direct_add:{inv.id}",
                    payload={"date": day.local_date.isoformat(), "shift": shift.kind, "invitation_id": str(inv.id)},
                    now=now)
    return True


def add_participant(db: Session, actor: Actor, shift_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> None:
    shift = _shift(db, shift_id)
    day = lock_day(db, shift.activity_day_id)
    require_manage_day(actor, day)
    _add_participant_locked(db, actor, day, shift, user_id, now)
    db.commit()


def remove_participant(db: Session, actor: Actor, shift_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> None:
    shift = _shift(db, shift_id)
    day = lock_day(db, shift.activity_day_id)
    require_manage_day(actor, day)
    row = db.scalar(select(ManagerAddedParticipant).where(
        ManagerAddedParticipant.shift_id == shift_id, ManagerAddedParticipant.user_id == user_id,
        ManagerAddedParticipant.removed_at.is_(None)))
    if row is None:
        raise not_found("participant_not_found", "המשתתף לא נמצא ברשימה")
    row.removed_at, row.removed_by_id = now, actor.user_id
    record(db, actor_id=actor.user_id, action="participant_remove", object_type="shift", object_id=shift_id, now=now,
           meta={"user": str(user_id)})
    db.commit()


def invite(db: Session, actor: Actor, day_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> Invitation:
    day = lock_day(db, day_id)
    require_manage_day(actor, day)
    _check_assignable_user(db, user_id)
    if not _is_guest(db, day, user_id):
        raise conflict("already_eligible", "המתנדב כבר יכול להירשם ליום זה")
    if now >= local_midnight_after(day.local_date):
        raise conflict("day_ended", "התאריך כבר עבר")
    existing = db.scalar(select(Invitation).where(Invitation.user_id == user_id, Invitation.activity_day_id == day.id,
                                                  Invitation.kind == "invitation",
                                                  Invitation.status.in_(("pending", "confirmed"))))
    if existing is not None:
        return existing
    inv = Invitation(user_id=user_id, activity_day_id=day.id, invited_by_id=actor.user_id, kind="invitation",
                     status="pending", created_at=now, expires_at=local_midnight_after(day.local_date))
    db.add(inv)
    db.flush()
    enqueue(db, event_type="invitation", recipients=[user_id], dedup_prefix=f"invitation:{inv.id}",
            payload={"date": day.local_date.isoformat(), "invitation_id": str(inv.id)}, now=now)
    record(db, actor_id=actor.user_id, action="invite", object_type="activity_day", object_id=day.id, now=now,
           meta={"user": str(user_id)})
    db.commit()
    return inv


def set_order(db: Session, actor: Actor, shift_id: uuid.UUID, user_ids: list[uuid.UUID], now: datetime) -> None:
    shift = _shift(db, shift_id)
    day = lock_day(db, shift.activity_day_id)
    require_manage_day(actor, day)
    if len(set(user_ids)) != len(user_ids):
        raise bad_request("duplicate_users", "רשימה כפולה")
    db.execute(delete(QueueOrder).where(QueueOrder.shift_id == shift_id, QueueOrder.user_id.in_(user_ids)))
    for i, uid in enumerate(user_ids):
        db.add(QueueOrder(shift_id=shift_id, user_id=uid, rank=float(i + 1), updated_at=now, updated_by_id=actor.user_id))
    record(db, actor_id=actor.user_id, action="queue_order", object_type="shift", object_id=shift_id, now=now,
           meta={"count": len(user_ids)})
    db.commit()


def set_morning_registration(db: Session, actor: Actor, shift_id: uuid.UUID, open_: bool, now: datetime) -> bool:
    """Explicitly open (or close) self-registration for a Sunday–Friday morning shift. Saturday morning
    is always open. Closing hides the shift from registration screens; answers already given are kept
    (they stay in the manager's pool) and assignments/history are untouched."""
    shift = _shift(db, shift_id)
    day = lock_day(db, shift.activity_day_id)
    require_manage_day(actor, day)
    if shift.kind != "morning":
        raise bad_request("not_morning", "אפשר לפתוח או לסגור להרשמה רק משמרת בוקר")
    if local_weekday(day.local_date) == SATURDAY:
        raise conflict("saturday_fixed", "משמרת בוקר בשבת פתוחה תמיד להרשמה")
    if now >= local_midnight_after(day.local_date):
        raise conflict("day_ended", "התאריך כבר עבר")
    if shift.self_selectable == open_:
        db.rollback()
        return False
    shift.self_selectable = open_
    day.row_version += 1
    record(db, actor_id=actor.user_id, action="morning_registration_open" if open_ else "morning_registration_close",
           object_type="shift", object_id=shift.id, now=now, meta={"date": day.local_date.isoformat()})
    db.commit()
    return True


def resolve_attention(db: Session, actor: Actor, attention_id: uuid.UUID, now: datetime) -> None:
    att = db.get(AvailabilityAttention, attention_id)
    if att is None:
        raise not_found()
    shift = _shift(db, att.shift_id)
    day = lock_day(db, shift.activity_day_id)
    require_manage_day(actor, day)
    if att.status == "pending":
        att.status, att.resolved_at, att.resolved_by_id, att.resolution = "resolved", now, actor.user_id, "manager"
        record(db, actor_id=actor.user_id, action="attention_resolve", object_type="availability_attention",
               object_id=att.id, now=now)
    db.commit()


# ---------------- publication ----------------

BOARDING = "boarding"  # snapshot marker: scheduled on the boarding order, no ambulance


def _snapshot(db: Session, version_id: uuid.UUID | None) -> tuple[dict, dict]:
    """(shift_id -> {user_id: slot_key}, shift_id -> {active slot_key: capacity})"""
    people: dict[uuid.UUID, dict[uuid.UUID, uuid.UUID]] = {}
    slots: dict[uuid.UUID, dict[uuid.UUID, int]] = {}
    if version_id is None:
        return people, slots
    key_of = {}
    for s in db.scalars(select(AmbulanceSlot).where(AmbulanceSlot.version_id == version_id)):
        key_of[s.id] = s.slot_key
        if s.status == "active":
            slots.setdefault(s.shift_id, {})[s.slot_key] = s.capacity
    for a in db.scalars(select(Assignment).where(Assignment.version_id == version_id)):
        people.setdefault(a.shift_id, {})[a.user_id] = key_of[a.slot_id]
    # A trainee on the boarding order is scheduled even without an ambulance. Ambulance placement takes precedence.
    for b in db.scalars(select(BoardingEntry).where(BoardingEntry.version_id == version_id, live_boarding(BoardingEntry))):
        people.setdefault(b.shift_id, {}).setdefault(b.user_id, BOARDING)
    return people, slots


def _add_baseline_slots(db: Session, version_id: uuid.UUID, shift: Shift) -> None:
    for i in range(shift.baseline_slots):
        db.add(AmbulanceSlot(version_id=version_id, shift_id=shift.id, ordinal=i + 1, capacity=DEFAULT_CAPACITY))


def add_shift_type(db: Session, actor: Actor, day_id: uuid.UUID, kind: str, now: datetime) -> Shift:
    """Adds an allowed shift type to a date (decision 2026-10-16). Allowed after registration opens, including
    after publication. It never reopens registration or joining: after publication the new type is not
    self-selectable and volunteers cannot request it. The Day Manager assigns people directly. Published rows
    are untouched; the working copy gets the type's baseline ambulances. Volunteers of the date's home day are
    notified."""
    from app.days.service import allowed_shift_kinds, build_shift, template_for
    from app.accounts.models import DayMembership
    day = lock_day(db, day_id)
    require_manage_day(actor, day)
    if kind not in allowed_shift_kinds(day.kind):
        raise bad_request("shift_not_allowed", "סוג המשמרת אינו מותר ליום זה")
    if db.scalar(select(Shift.id).where(Shift.activity_day_id == day.id, Shift.kind == kind)) is not None:
        raise conflict("shift_exists", "המשמרת הזו כבר קיימת ביום זה")
    shift = build_shift(day, template_for(day.kind, kind))
    if day.latest_published_version_id is not None:
        shift.self_selectable = False  # publication closes joining; a new type must not open it
    db.add(shift)
    db.flush()
    draft = current_draft(db, day.id)
    if draft is not None:
        _add_baseline_slots(db, draft.id, shift)
    wd = local_weekday(day.local_date)
    recipients = list(db.scalars(
        select(User.id).join(DayMembership, DayMembership.user_id == User.id)
        .where(DayMembership.weekday == wd, DayMembership.valid_to.is_(None), User.status == "active")))
    enqueue(db, event_type="shift_type_added", recipients=recipients,
            dedup_prefix=f"shift_added:{shift.id}",
            payload={"date": day.local_date.isoformat(), "activity_day_id": str(day.id), "shift": kind}, now=now)
    record(db, actor_id=actor.user_id, action="shift_type_add", object_type="shift", object_id=shift.id, now=now,
           meta={"date": day.local_date.isoformat(), "kind": kind, "notified": len(recipients)})
    db.commit()
    return shift


def set_slot_operator(db: Session, actor: Actor, slot_key: uuid.UUID, user_id: uuid.UUID | None,
                      start: time | None, end: time | None, now: datetime) -> None:
    """Records the volunteer who operates an additional ambulance (decision 2026-10-16). Start only means from
    that time until the end of the shift; start and end give the explicit range. Local (Asia/Jerusalem) times.
    user_id None clears the operator. Draft only."""
    from app.core.timeutil import to_local
    _, draft = _begin_edit(db, actor, _slot_day_id(db, slot_key), now)
    slot = _draft_slot(db, draft, slot_key)
    if user_id is None:
        slot.operator_user_id, slot.operator_start, slot.operator_end = None, None, None
    else:
        _check_assignable_user(db, user_id)
        if start is None:
            raise bad_request("operator_start_required", "יש להזין שעת התחלה למפעיל/ה")
        shift = _shift(db, slot.shift_id)
        s_local, e_local = to_local(shift.starts_at).time(), to_local(shift.ends_at).time()
        if not (s_local <= start < e_local):
            raise bad_request("operator_outside_shift", "שעת ההתחלה חייבת להיות בתוך המשמרת")
        if end is not None and not (start < end <= e_local):
            raise bad_request("operator_bad_range", "שעת הסיום חייבת להיות אחרי ההתחלה ולא אחרי סיום המשמרת")
        slot.operator_user_id, slot.operator_start, slot.operator_end = user_id, start, end
    record(db, actor_id=actor.user_id, action="draft_slot_operator", object_type="schedule_version",
           object_id=draft.id, now=now, meta={"slot_key": str(slot_key), "operator": str(user_id) if user_id else None})
    db.commit()


def set_boarding(db: Session, actor: Actor, day_id: uuid.UUID, entries: list[tuple[uuid.UUID, uuid.UUID]],
                 now: datetime) -> None:
    """Replaces the date's boarding order (סדר עלייה) in the working copy. entries are (user_id, shift_id) in
    boarding order. Only trainees, only evening or reinforcement shifts of this date, no duplicates. Being on
    the list is scheduled participation; it does not assign an ambulance."""
    day, draft = _begin_edit(db, actor, day_id, now)
    shifts = {s.id: s for s in day_shifts(db, day.id)}
    seen: set[uuid.UUID] = set()
    for user_id, shift_id in entries:
        if shift_id not in shifts or shifts[shift_id].kind not in ("evening", "reinforcement"):
            raise bad_request("boarding_shift", "סדר העלייה מיועד למשמרת ערב או תגבור של היום")
        if user_id in seen:
            raise bad_request("boarding_duplicate", "אותו חניך/ה מופיע פעמיים בסדר העלייה")
        seen.add(user_id)
        if _check_assignable_user(db, user_id) != "trainee":
            raise bad_request("boarding_not_trainee", "בסדר העלייה ניתן לרשום חניכים בלבד")
    db.execute(delete(BoardingEntry).where(BoardingEntry.version_id == draft.id))
    db.flush()
    for position, (user_id, shift_id) in enumerate(entries, start=1):
        db.add(BoardingEntry(version_id=draft.id, shift_id=shift_id, user_id=user_id, position=position))
    record(db, actor_id=actor.user_id, action="draft_boarding", object_type="schedule_version", object_id=draft.id,
           now=now, meta={"count": len(entries)})
    db.commit()


def publish(db: Session, actor: Actor, day_id: uuid.UUID, draft_id: uuid.UUID, expected_edit_count: int,
            now: datetime) -> dict:
    day = lock_day(db, day_id)
    require_manage_day(actor, day)
    version = db.scalar(select(ScheduleVersion).where(ScheduleVersion.id == draft_id,
                                                      ScheduleVersion.activity_day_id == day.id).with_for_update())
    if version is None:
        raise not_found("draft_not_found", "הטיוטה לא נמצאה")
    if version.status != "draft":
        # Retry of a publication that already happened: no second state change, no new messages.
        return {"published_version_id": str(version.id), "revision": version.revision, "already_published": True}
    if version.edit_count != expected_edit_count:
        raise conflict("stale_draft", "הטיוטה השתנתה מאז שנטענה. יש לרענן ולבדוק לפני פרסום",
                       {"edit_count": version.edit_count})
    if day.latest_published_version_id is not None and version.edit_count == 0:
        raise conflict("nothing_to_publish", "אין שינויים לפרסום")
    # Requests must be decided first: approve or reject every pending request (decisions do not assign anyone).
    from app.registration.service import pending_request_count
    pending = pending_request_count(db, day.id)
    if pending:
        raise conflict("pending_requests",
                       f"לא ניתן לפרסם: יש {pending} בקשות להרשמה שממתינות להחלטה. יש לאשר או לדחות כל בקשה לפני הפרסום",
                       {"pending": pending})

    # Re-validate hard invariants on the whole draft (defence in depth beyond per-edit checks).
    shifts = {s.id: s for s in day_shifts(db, day.id)}
    for (user_id,) in db.execute(select(Assignment.user_id).where(Assignment.version_id == version.id)
                                 .group_by(Assignment.user_id).having(func.count() > 1)).all():
        kinds = {shifts[sid].kind for sid in db.scalars(select(Assignment.shift_id).where(
            Assignment.version_id == version.id, Assignment.user_id == user_id))}
        if {"reinforcement", "evening"} <= kinds and current_level(db, user_id) == "trainee":
            raise conflict("trainee_reinforcement_evening", "חניך משובץ גם בתגבור וגם בערב — יש לתקן לפני פרסום")

    previous_id = day.latest_published_version_id
    if previous_id is not None:
        prev = db.get(ScheduleVersion, previous_id)
        prev.status = "superseded"
    version.status = "published"
    version.published_at = now
    version.published_by_id = actor.user_id
    day.latest_published_version_id = version.id
    day.row_version += 1
    db.flush()

    sent = _publication_notifications(db, day, shifts, previous_id, version, now)
    _resolve_attentions_after_publish(db, shifts, version.id, now)
    record(db, actor_id=actor.user_id, action="publish", object_type="activity_day", object_id=day.id, now=now,
           meta={"version": str(version.id), "revision": version.revision, "notifications": sent})
    db.commit()
    return {"published_version_id": str(version.id), "revision": version.revision, "already_published": False,
            "notifications_queued": sent}


# One message per person per publication. When a person is affected in several shifts of the same
# publication (e.g. assigned to evening and waiting for morning, or a trainee waiting for both
# evening and reinforcement), the message type is the most personal change and the payload lists
# every affected shift. Order = priority.
_EVENT_FOR_CHANGE = {
    "assigned": "assigned",
    "unassigned": "unassigned",
    "changed": "schedule_changed",
    "waiting_new_ambulance": "new_ambulance",
    "waiting": "schedule_changed_waiting",
    "published_waiting": "schedule_published",
}
_PRIORITY = list(_EVENT_FOR_CHANGE)


def _day_schedule_changed(shifts, new_people, old_people, new_slots, old_slots) -> bool:
    """True if any shift of the date gained, lost or moved an assignment, or changed its ambulance slots."""
    for shift_id in shifts:
        new_map, old_map = new_people.get(shift_id, {}), old_people.get(shift_id, {})
        if set(new_map) != set(old_map):
            return True
        if any(new_map[u] != old_map[u] for u in new_map):
            return True
        if set(new_slots.get(shift_id, {})) != set(old_slots.get(shift_id, {})):
            return True
    return False


def _publication_notifications(db: Session, day: ActivityDay, shifts: dict, previous_id, version, now) -> int:
    """Recipients of a publication (decision 2026-10-16, supersedes the 2026-10-12 'affected shift only' rule):
    every volunteer assigned to the date's schedule in the new version, every volunteer waiting for that date
    (the derived pool minus the assigned, the same list the manager board shows), and every volunteer removed
    from it. Removed people get 'unassigned'. Only called from publish(), so draft edits never notify.
    Deduplicated per person per publication (dedup key = version + user), so a retried publish adds nothing and
    a later publication notifies again."""
    new_people, new_slots = _snapshot(db, version.id)
    old_people, old_slots = _snapshot(db, previous_id)
    pools = shift_pools(db, day, list(shifts.values()))
    first = previous_id is None

    if not first and not _day_schedule_changed(shifts, new_people, old_people, new_slots, old_slots):
        return 0  # a republication with no net change to the schedule notifies nobody

    changes: dict[uuid.UUID, list[tuple[str, str]]] = {}  # user -> [(shift kind, change)]
    for shift_id, shift in shifts.items():
        new_map, old_map = new_people.get(shift_id, {}), old_people.get(shift_id, {})
        added = set(new_map) - set(old_map)
        removed = set(old_map) - set(new_map)
        new_slot_keys = set(new_slots.get(shift_id, {})) - set(old_slots.get(shift_id, {}))
        waiting = set(pools.get(shift_id, {})) - set(new_map) - removed
        if first:
            waiting_change = "published_waiting"
        else:
            waiting_change = "waiting_new_ambulance" if new_slot_keys else "waiting"
        for uid in added:
            changes.setdefault(uid, []).append((shift.kind, "assigned"))
        for uid in removed:
            changes.setdefault(uid, []).append((shift.kind, "unassigned"))
        for uid in set(new_map) - added:
            changes.setdefault(uid, []).append((shift.kind, "changed"))
        for uid in waiting:
            changes.setdefault(uid, []).append((shift.kind, waiting_change))

    if not changes:
        return 0
    active = set(db.scalars(select(User.id).where(User.id.in_(list(changes)), User.status == "active")))
    base = {"date": day.local_date.isoformat(), "activity_day_id": str(day.id), "revision": version.revision}
    count = 0
    for uid in sorted(changes, key=str):
        if uid not in active:
            continue
        items = changes[uid]
        kind = min((c for _, c in items), key=_PRIORITY.index)
        shifts_out = [{"shift": k, "change": c} for k, c in items]
        # Someone only removed from the schedule gets no schedule references.
        only_removed = all(c == "unassigned" for _, c in items)
        payload = {"date": base["date"], "shifts": shifts_out} if only_removed else {**base, "shifts": shifts_out}
        count += enqueue(db, event_type=_EVENT_FOR_CHANGE[kind], recipients=[uid],
                         dedup_prefix=f"publication:{version.id}", payload=payload, now=now)
    return count


def _resolve_attentions_after_publish(db: Session, shifts: dict, version_id: uuid.UUID, now: datetime) -> None:
    assigned = assigned_by_shift(db, version_id)
    for att in db.scalars(select(AvailabilityAttention).where(AvailabilityAttention.shift_id.in_(list(shifts)),
                                                              AvailabilityAttention.status == "pending")):
        if att.user_id not in assigned.get(att.shift_id, {}):
            att.status, att.resolved_at, att.resolution = "resolved", now, "unassigned_in_publication"
