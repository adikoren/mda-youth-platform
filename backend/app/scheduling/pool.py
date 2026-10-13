"""Per-shift candidate pool and derived waiting lists.

pool(shift)   = self-declared availability for that shift
                (+ for ordinary reinforcement: trainees available for that date's evening)
                + manager-added participants (provenance kept)
waiting(shift, version) = pool - people assigned to that shift in that version,
                ordered by the manager's persisted manual rank.
Availability is per shift, so assigning evening never removes someone from morning waiting."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.models import DayMembership, User, VolunteerLevel
from app.days.models import ActivityDay, Shift
from app.registration.models import ManagerAddedParticipant, QueueOrder, Registration, RegistrationChoice
from app.scheduling.models import Assignment


@dataclass
class PoolEntry:
    user_id: uuid.UUID
    self_available: bool = False
    manager_added: bool = False
    via_evening: bool = False  # reinforcement candidate derived from evening availability
    arrival_time: time | None = None
    is_late: bool = False
    answered_at: datetime | None = None
    rank: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def source(self) -> str:
        if self.self_available and self.manager_added:
            return "both"
        return "manager" if self.manager_added else "self"


@dataclass
class PersonInfo:
    user_id: uuid.UUID
    full_name: str
    level: str | None
    home_weekday: int | None
    phone: str | None


def people(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, PersonInfo]:
    if not user_ids:
        return {}
    rows = db.execute(
        select(User.id, User.full_name, User.phone, VolunteerLevel.level, DayMembership.weekday)
        .outerjoin(VolunteerLevel, (VolunteerLevel.user_id == User.id) & VolunteerLevel.valid_to.is_(None))
        .outerjoin(DayMembership, (DayMembership.user_id == User.id) & DayMembership.valid_to.is_(None))
        .where(User.id.in_(user_ids))
    ).all()
    return {r[0]: PersonInfo(r[0], r[1], r[3], r[4], r[2]) for r in rows}


def shift_pools(db: Session, day: ActivityDay, shifts: list[Shift]) -> dict[uuid.UUID, dict[uuid.UUID, PoolEntry]]:
    pools: dict[uuid.UUID, dict[uuid.UUID, PoolEntry]] = {s.id: {} for s in shifts}
    by_kind = {s.kind: s for s in shifts}
    choices = db.execute(
        select(RegistrationChoice, Registration.user_id)
        .join(Registration, Registration.id == RegistrationChoice.registration_id)
        .where(Registration.activity_day_id == day.id, RegistrationChoice.available.is_(True))
    ).all()
    for choice, user_id in choices:
        if choice.shift_id in pools:
            pools[choice.shift_id][user_id] = PoolEntry(
                user_id, self_available=True, arrival_time=choice.arrival_time, is_late=choice.is_late,
                answered_at=choice.first_answered_at)

    reinforcement, evening = by_kind.get("reinforcement"), by_kind.get("evening")
    if reinforcement is not None and evening is not None:
        evening_ids = set(pools[evening.id])
        if evening_ids:
            trainees = set(db.scalars(select(VolunteerLevel.user_id).where(
                VolunteerLevel.user_id.in_(evening_ids), VolunteerLevel.valid_to.is_(None),
                VolunteerLevel.level == "trainee")))
            for uid in trainees:
                src = pools[evening.id][uid]
                pools[reinforcement.id][uid] = PoolEntry(uid, via_evening=True, is_late=src.is_late,
                                                         answered_at=src.answered_at)

    added = db.scalars(select(ManagerAddedParticipant).where(
        ManagerAddedParticipant.shift_id.in_(list(pools)), ManagerAddedParticipant.removed_at.is_(None)))
    for a in added:
        entry = pools[a.shift_id].setdefault(a.user_id, PoolEntry(a.user_id, answered_at=a.added_at))
        entry.manager_added = True

    ranks = db.execute(select(QueueOrder.shift_id, QueueOrder.user_id, QueueOrder.rank)
                       .where(QueueOrder.shift_id.in_(list(pools)))).all()
    for shift_id, user_id, rank in ranks:
        if user_id in pools[shift_id]:
            pools[shift_id][user_id].rank = rank
    return pools


def assigned_by_shift(db: Session, version_id: uuid.UUID | None) -> dict[uuid.UUID, dict[uuid.UUID, Assignment]]:
    out: dict[uuid.UUID, dict[uuid.UUID, Assignment]] = {}
    if version_id is None:
        return out
    for a in db.scalars(select(Assignment).where(Assignment.version_id == version_id)):
        out.setdefault(a.shift_id, {})[a.user_id] = a
    return out


def ordered(entries: list[PoolEntry]) -> list[PoolEntry]:
    far = datetime.max.replace(tzinfo=None)
    return sorted(entries, key=lambda e: (e.rank is None, e.rank or 0.0,
                                          (e.answered_at.replace(tzinfo=None) if e.answered_at else far)))
