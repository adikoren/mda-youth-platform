"""Read models for schedule screens. Every function here assumes the caller already passed the
matching access policy."""
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.core.timeutil import SATURDAY, WEEKDAY_NAMES_HE, local_today, local_weekday
from app.days.models import ActivityDay, Shift
from app.registration.models import AvailabilityAttention, Invitation, Registration, RegistrationChoice
from app.registration.service import window_state
from app.scheduling.models import AmbulanceSlot, Assignment, BoardingEntry, ScheduleVersion
from app.scheduling.liveness import live_assignment, live_boarding
from app.scheduling.pool import assigned_by_shift, ordered, people, shift_pools
from app.scheduling.service import MAX_CAPACITY, current_draft, day_shifts


def day_summary(day: ActivityDay, now: datetime) -> dict:
    wd = local_weekday(day.local_date)
    return {
        "id": str(day.id), "date": day.local_date.isoformat(), "weekday": wd, "weekday_name": WEEKDAY_NAMES_HE[wd],
        "kind": day.kind, "registration_opens_at": day.registration_opens_at,
        "registration_closes_at": day.registration_closes_at, "publish_target_at": day.publish_target_at,
        "window": window_state(day, now), "is_published": day.latest_published_version_id is not None,
        "row_version": day.row_version,
    }


def capacity_warning(capacity: int, count: int) -> str | None:
    if count > capacity:
        return f"הוגדרו {capacity} מקומות, אך משובצים {count} מתנדבים"
    return None


def _shift_out(s: Shift) -> dict:
    return {"id": str(s.id), "kind": s.kind, "starts_at": s.starts_at, "ends_at": s.ends_at, "arrival_at": s.arrival_at,
            "self_selectable": s.self_selectable,
            # Sunday–Friday mornings are opened for registration explicitly by a manager.
            "registration_toggle": s.kind == "morning" and local_weekday(local_today(s.starts_at)) != SATURDAY}


# Crew order inside an ambulance (decision 2026-10-16): mentor, then youth volunteer, then trainee positions.
ROLE_ORDER = {"mentor": 0, "youth": 1, "trainee": 2}


def crew_sort_key(person: dict) -> tuple:
    return (ROLE_ORDER.get(person.get("level"), 3), person["full_name"])


def _operator_out(slot: AmbulanceSlot, info: dict) -> dict | None:
    """The volunteer who operates an additional ambulance: name plus local start (and end) time. Kept apart from
    the youth volunteer role and from guest labels."""
    if slot.operator_user_id is None:
        return None
    p = info.get(slot.operator_user_id)
    return {"user_id": str(slot.operator_user_id), "full_name": p.full_name if p else "",
            "start": slot.operator_start.strftime("%H:%M") if slot.operator_start else None,
            "end": slot.operator_end.strftime("%H:%M") if slot.operator_end else None}


def _home_info(p) -> dict:
    """A guest's actual home weekday, shown next to the name (kept separate from role labels)."""
    wd = p.home_weekday
    return {"home_weekday": wd, "home_weekday_name": WEEKDAY_NAMES_HE[wd] if wd is not None else None}


def _person_out(p, extra: dict | None = None) -> dict:
    out = {"user_id": str(p.user_id), "full_name": p.full_name, "level": p.level}
    out.update(extra or {})
    return out


def published_schedule(db: Session, actor: Actor, day: ActivityDay, now: datetime, include_phone: bool = False) -> dict:
    version = db.get(ScheduleVersion, day.latest_published_version_id)
    shifts = day_shifts(db, day.id)
    slots = list(db.scalars(select(AmbulanceSlot).where(AmbulanceSlot.version_id == version.id,
                                                        AmbulanceSlot.status == "active")
                            .order_by(AmbulanceSlot.ordinal)))
    assignments = list(db.scalars(select(Assignment).where(Assignment.version_id == version.id, live_assignment())))
    boarding = list(db.execute(select(BoardingEntry, Shift.kind).join(Shift, Shift.id == BoardingEntry.shift_id)
                               .where(BoardingEntry.version_id == version.id, live_boarding(BoardingEntry))
                               .order_by(BoardingEntry.position)).all())
    info = people(db, {a.user_id for a in assignments} | {b.user_id for b, _ in boarding}
                  | {sl.operator_user_id for sl in slots if sl.operator_user_id})
    ordinal_of = {sl.id: sl.ordinal for sl in slots}
    placed = {a.user_id: ordinal_of.get(a.slot_id) for a in assignments}
    wd = local_weekday(day.local_date)
    by_slot: dict[uuid.UUID, list] = {}
    for a in assignments:
        p = info[a.user_id]
        extra = {"is_me": a.user_id == actor.user_id, "is_guest": wd != SATURDAY and p.home_weekday != wd,
                 **_home_info(p)}
        if include_phone:
            extra["phone"] = p.phone
        by_slot.setdefault(a.slot_id, []).append(_person_out(p, extra))
    out_shifts = []
    for s in shifts:
        # Published view: only ambulances with at least one assigned volunteer, and only shifts with at least one
        # such ambulance. Empty records stay in the management editor (the board) and are not deleted.
        populated = [sl for sl in slots if sl.shift_id == s.id and by_slot.get(sl.id)]
        if not populated:
            continue
        out_shifts.append({**_shift_out(s), "slots": [
            {"slot_key": str(sl.slot_key), "ordinal": sl.ordinal, "capacity": sl.capacity,
             "crew": sorted(by_slot[sl.id], key=crew_sort_key),
             # Unfilled trainee positions on a populated ambulance (labelled חניך/ה in the UI).
             "empty_trainee_positions": max(0, sl.capacity - len(by_slot[sl.id])),
             "operator": _operator_out(sl, info)} for sl in populated]})
    boarding_out = [{"position": b.position, "shift": kind, "shift_id": str(b.shift_id),
                     **_person_out(info[b.user_id], {"is_me": b.user_id == actor.user_id,
                                                     "is_guest": wd != SATURDAY and info[b.user_id].home_weekday != wd,
                                                     **_home_info(info[b.user_id])}),
                     "ambulance": placed.get(b.user_id)}
                    for b, kind in boarding]
    mine = any(a.user_id == actor.user_id for a in assignments)
    assigned_outside_home = mine and wd != SATURDAY and actor.home_weekday != wd
    return {"day": day_summary(day, now), "revision": version.revision, "published_at": version.published_at,
            "shifts": out_shifts, "boarding": boarding_out, "assigned_outside_home": assigned_outside_home}


def board(db: Session, day: ActivityDay, now: datetime) -> dict:
    """Manager board: working draft (if any) beside the live published version."""
    draft = current_draft(db, day.id)
    shown_id = draft.id if draft else day.latest_published_version_id
    shifts = day_shifts(db, day.id)
    pools = shift_pools(db, day, shifts)
    assigned = assigned_by_shift(db, shown_id)
    published_assigned = assigned_by_shift(db, day.latest_published_version_id)
    slots = list(db.scalars(select(AmbulanceSlot).where(AmbulanceSlot.version_id == shown_id)
                            .order_by(AmbulanceSlot.ordinal))) if shown_id else []
    attentions = list(db.scalars(select(AvailabilityAttention).where(
        AvailabilityAttention.shift_id.in_([s.id for s in shifts]), AvailabilityAttention.status == "pending")))
    att_keys = {(a.shift_id, a.user_id): str(a.id) for a in attentions}
    confirmations = {(i.user_id): i.status for i in db.scalars(select(Invitation).where(
        Invitation.activity_day_id == day.id, Invitation.kind == "direct_add",
        Invitation.status.in_(("pending", "confirmed"))))}
    # Explicit cancellations ("בוטל"): kept visible to the Day Manager, never counted as candidates.
    cancelled_rows = db.execute(select(RegistrationChoice, Registration.user_id, Shift.id)
                                .join(Registration, Registration.id == RegistrationChoice.registration_id)
                                .join(Shift, Shift.id == RegistrationChoice.shift_id)
                                .where(Registration.activity_day_id == day.id,
                                       RegistrationChoice.cancelled_at.is_not(None))
                                .order_by(RegistrationChoice.cancelled_at)).all()
    cancelled_by_shift: dict[uuid.UUID, list[tuple[uuid.UUID, datetime]]] = {}
    for choice, uid, shift_id in cancelled_rows:
        cancelled_by_shift.setdefault(shift_id, []).append((uid, choice.cancelled_at))
    # Boarding order (סדר עלייה) of the shown version. Listed trainees are scheduled, so they are not waiting.
    boarding_rows = list(db.execute(select(BoardingEntry, Shift.kind).join(Shift, Shift.id == BoardingEntry.shift_id)
                                    .where(BoardingEntry.version_id == shown_id, live_boarding(BoardingEntry))
                                    .order_by(BoardingEntry.position)).all()) if shown_id else []
    boarded_ids = {b.user_id for b, _ in boarding_rows}
    all_ids = {u for p in pools.values() for u in p} | {u for m in assigned.values() for u in m} \
        | {u for rows in cancelled_by_shift.values() for u, _ in rows} | boarded_ids \
        | {sl.operator_user_id for sl in slots if sl.operator_user_id}
    info = people(db, all_ids)
    from app.mentoring.service import progress_for  # context only: never a priority formula
    progress = progress_for(db, [u for u, p in info.items() if p.level == "trainee"])
    wd = local_weekday(day.local_date)
    slot_key_of = {sl.id: sl.slot_key for sl in slots}

    def person(uid: uuid.UUID, shift_id: uuid.UUID) -> dict:
        p = info[uid]
        entry = pools.get(shift_id, {}).get(uid)
        other_shifts = [s.kind for s in shifts if s.id != shift_id and uid in assigned.get(s.id, {})]
        return _person_out(p, {
            "is_guest": wd != SATURDAY and p.home_weekday != wd,
            **_home_info(p),
            "source": entry.source if entry else "assigned_only",
            "via_evening": bool(entry and entry.via_evening),
            "arrival_time": entry.arrival_time.strftime("%H:%M") if entry and entry.arrival_time else None,
            "is_late": bool(entry and entry.is_late),
            "attention_id": att_keys.get((shift_id, uid)),
            "confirmation": confirmations.get(uid),
            "assigned_other_shifts": other_shifts,
            "progress": progress.get(uid),
        })

    ordinal_of = {sl.id: sl.ordinal for sl in slots}
    placed_by_user = {a.user_id: ordinal_of.get(a.slot_id) for m in assigned.values() for a in m.values()}
    out_shifts = []
    for s in shifts:
        s_slots = [sl for sl in slots if sl.shift_id == s.id]
        crew_by_slot: dict[uuid.UUID, list] = {}
        for uid, a in assigned.get(s.id, {}).items():
            crew_by_slot.setdefault(a.slot_id, []).append(person(uid, s.id))
        waiting = [person(e.user_id, s.id) | {"rank": e.rank}
                   for e in ordered([e for uid, e in pools[s.id].items()
                                     if uid not in assigned.get(s.id, {}) and uid not in boarded_ids])]
        out_shifts.append({**_shift_out(s), "slots": [
            {"slot_key": str(sl.slot_key), "ordinal": sl.ordinal, "capacity": sl.capacity, "status": sl.status,
             "crew": sorted(crew_by_slot.get(sl.id, []), key=crew_sort_key),
             "empty_trainee_positions": max(0, sl.capacity - len(crew_by_slot.get(sl.id, []))),
             "operator": _operator_out(sl, info),
             "warning": capacity_warning(sl.capacity, len(crew_by_slot.get(sl.id, []))) if sl.status == "active" else None,
             # Legacy value above today's limit: kept as is, shown for review (never changed silently).
             "capacity_over_limit": sl.capacity > MAX_CAPACITY}
            for sl in s_slots], "waiting": waiting,
            "published_crew_count": len(published_assigned.get(s.id, {})),
            "cancelled": [_person_out(info[uid], {"cancelled_at": at.isoformat()})
                          for uid, at in cancelled_by_shift.get(s.id, [])]})
    boarding_out = [{"position": b.position, "shift": kind, "shift_id": str(b.shift_id),
                     **_person_out(info[b.user_id], {"is_guest": wd != SATURDAY and info[b.user_id].home_weekday != wd,
                                                     **_home_info(info[b.user_id])}),
                     "ambulance": placed_by_user.get(b.user_id)}
                    for b, kind in boarding_rows]
    published = db.get(ScheduleVersion, day.latest_published_version_id) if day.latest_published_version_id else None
    return {
        "boarding": boarding_out,
        "day": day_summary(day, now),
        "draft": None if draft is None else {
            "id": str(draft.id), "revision": draft.revision, "edit_count": draft.edit_count,
            "dirty": draft.edit_count > 0, "last_edit_at": draft.last_edit_at},
        "published": None if published is None else {
            "id": str(published.id), "revision": published.revision, "published_at": published.published_at},
        "showing": "draft" if draft else ("published" if published else "none"),
        "shifts": out_shifts,
        "attentions": [{"id": str(a.id), "shift_id": str(a.shift_id), "user_id": str(a.user_id),
                        "full_name": info[a.user_id].full_name if a.user_id in info else None,
                        "label": "ביטל זמינות — נדרש טיפול", "created_at": a.created_at} for a in attentions],
        "slot_keys": {str(k): str(v) for k, v in slot_key_of.items()},
    }


def registrations_table(db: Session, day: ActivityDay) -> list[dict]:
    rows = db.execute(select(Registration, RegistrationChoice, Shift.kind)
                      .join(RegistrationChoice, RegistrationChoice.registration_id == Registration.id)
                      .join(Shift, Shift.id == RegistrationChoice.shift_id)
                      .where(Registration.activity_day_id == day.id)).all()
    info = people(db, {r.user_id for r, _, _ in rows})
    out: dict[uuid.UUID, dict] = {}
    for reg, choice, kind in rows:
        p = info[reg.user_id]
        item = out.setdefault(reg.user_id, {"user_id": str(reg.user_id), "full_name": p.full_name, "level": p.level,
                                            "updated_at": reg.updated_at, "choices": {}})
        item["choices"][kind] = {"available": choice.available,
                                 "cancelled_at": choice.cancelled_at.isoformat() if choice.cancelled_at else None,
                                 "arrival_time": choice.arrival_time.strftime("%H:%M") if choice.arrival_time else None,
                                 "is_late": choice.is_late}
    return sorted(out.values(), key=lambda x: x["full_name"])


def my_history(db: Session, actor: Actor, until: date | None = None) -> list[dict]:
    """Permanent personal history: the person's assignments in each date's latest published
    version (planned participation, not attendance). Grants nothing beyond these rows."""
    q = (select(ActivityDay, Shift, AmbulanceSlot.ordinal)
         .join(Assignment, Assignment.version_id == ActivityDay.latest_published_version_id)
         .join(Shift, Shift.id == Assignment.shift_id)
         .join(AmbulanceSlot, AmbulanceSlot.id == Assignment.slot_id)
         .where(Assignment.user_id == actor.user_id, live_assignment())
         .order_by(ActivityDay.local_date.desc(), Shift.starts_at))
    # Boarding-order places count as participation too (ambulance None until placed).
    qb = (select(ActivityDay, Shift)
          .join(BoardingEntry, BoardingEntry.version_id == ActivityDay.latest_published_version_id)
          .join(Shift, Shift.id == BoardingEntry.shift_id)
          .where(BoardingEntry.user_id == actor.user_id, live_boarding(BoardingEntry))
          .order_by(ActivityDay.local_date.desc(), Shift.starts_at))
    rows = [(d, s, ordinal) for d, s, ordinal in db.execute(q).all()]
    rows += [(d, s, None) for d, s in db.execute(qb).all()]
    rows.sort(key=lambda r: (r[0].local_date, r[1].starts_at, r[2] is None), reverse=True)
    return [{"activity_day_id": str(d.id), "date": d.local_date.isoformat(), "weekday": local_weekday(d.local_date),
             "weekday_name": WEEKDAY_NAMES_HE[local_weekday(d.local_date)], "kind": d.kind, "shift": s.kind,
             "starts_at": s.starts_at, "ends_at": s.ends_at, "ambulance": ordinal}
            for d, s, ordinal in rows]


def manager_day_flags(db: Session, days: list[ActivityDay]) -> dict[uuid.UUID, dict]:
    ids = [d.id for d in days]
    drafts = {v.activity_day_id: v for v in db.scalars(select(ScheduleVersion).where(
        ScheduleVersion.activity_day_id.in_(ids), ScheduleVersion.status == "draft"))}
    shift_day = {s.id: s.activity_day_id for s in db.scalars(select(Shift).where(Shift.activity_day_id.in_(ids)))}
    attention_days: dict[uuid.UUID, int] = {}
    for a in db.scalars(select(AvailabilityAttention).where(
            AvailabilityAttention.shift_id.in_(list(shift_day)), AvailabilityAttention.status == "pending")):
        attention_days[shift_day[a.shift_id]] = attention_days.get(shift_day[a.shift_id], 0) + 1
    return {d.id: {"draft_dirty": bool(drafts.get(d.id) and drafts[d.id].edit_count > 0),
                   "attention_count": attention_days.get(d.id, 0)} for d in days}
