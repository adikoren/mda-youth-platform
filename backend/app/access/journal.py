"""Journal access rules (part of the central access module).

Mentoring is implemented for EVENING shifts only (approved 2026-10-12): a formal mentor (current
level "mentor") who is in the same ambulance as a trainee in the date's latest published schedule
gets access from 13:30 on the shift date until 10:00 the next local morning. Morning and
reinforcement mentoring windows are deliberately undefined; those shifts grant no mentor access.

During the window a mentor may:
  * read the trainee's historical per-case records (descriptions + per-case assessments),
  * create rides and edit assessments for rides of the window's shift,
  * read/edit the shift-level mentoring summary of that shift only.
A mentor never sees previous shift-level summaries. Manager-granted extensions (reopened access)
behave like a window for the named mentor, trainee and shift."""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.access.context import Actor
from app.accounts.models import DayMembership, VolunteerLevel
from app.core.timeutil import local_dt, local_today
from app.days.models import ActivityDay, Shift
from app.mentoring.models import MentorAccessExtension
from app.scheduling.models import Assignment

EVENING_MENTOR_START = time(13, 30)
MENTOR_END_NEXT_DAY = time(10, 0)
MENTORED_SHIFT_KINDS = ("evening",)


def mentor_window(shift_date: date) -> tuple[datetime, datetime]:
    """[13:30 on the date, 10:00 next local day) as UTC instants (DST-safe)."""
    return local_dt(shift_date, EVENING_MENTOR_START), local_dt(shift_date + timedelta(days=1), MENTOR_END_NEXT_DAY)


def current_level(db: Session, user_id: uuid.UUID) -> str | None:
    return db.scalar(select(VolunteerLevel.level).where(VolunteerLevel.user_id == user_id,
                                                        VolunteerLevel.valid_to.is_(None)))


def home_weekday(db: Session, user_id: uuid.UUID) -> int | None:
    return db.scalar(select(DayMembership.weekday).where(DayMembership.user_id == user_id,
                                                         DayMembership.valid_to.is_(None)))


def crew_pairs(db: Session, shift_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """(shift_id, mentor_id, trainee_id) for formal mentors and trainees sharing an ambulance in the
    latest published version of each shift's date."""
    if not shift_ids:
        return []
    m, t = aliased(Assignment), aliased(Assignment)
    ml, tl = aliased(VolunteerLevel), aliased(VolunteerLevel)
    q = (select(m.shift_id, m.user_id, t.user_id)
         .join(Shift, Shift.id == m.shift_id)
         .join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
         .join(t, (t.slot_id == m.slot_id) & (t.version_id == m.version_id) & (t.user_id != m.user_id))
         .join(ml, (ml.user_id == m.user_id) & ml.valid_to.is_(None) & (ml.level == "mentor"))
         .join(tl, (tl.user_id == t.user_id) & tl.valid_to.is_(None) & (tl.level == "trainee"))
         .where(m.version_id == ActivityDay.latest_published_version_id, m.shift_id.in_(shift_ids),
                Shift.kind.in_(MENTORED_SHIFT_KINDS)))
    return [tuple(r) for r in db.execute(q).all()]


def formal_mentors(db: Session, trainee_id: uuid.UUID, shift_id: uuid.UUID) -> set[uuid.UUID]:
    return {m for s, m, t in crew_pairs(db, [shift_id]) if t == trainee_id}


def mentor_grant_ends(db: Session, actor: Actor, now: datetime) -> dict[uuid.UUID, dict[uuid.UUID, datetime]]:
    """trainee_id -> {shift_id: end of the current access} for windows and active extensions.
    The end instant also bounds how long the client may keep an offline copy (D-44)."""
    grants: dict[uuid.UUID, dict[uuid.UUID, datetime]] = {}
    if not actor.is_active or actor.level != "mentor":
        return grants

    def grant(trainee_id, shift_id, ends_at):
        current = grants.setdefault(trainee_id, {}).get(shift_id)
        grants[trainee_id][shift_id] = max(current, ends_at) if current else ends_at

    today = local_today(now)
    shifts = db.execute(select(Shift.id, ActivityDay.local_date).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                        .where(ActivityDay.local_date.in_([today, today - timedelta(days=1)]),
                               Shift.kind.in_(MENTORED_SHIFT_KINDS))).all()
    open_ends = {sid: mentor_window(d)[1] for sid, d in shifts if mentor_window(d)[0] <= now < mentor_window(d)[1]}
    for shift_id, mentor_id, trainee_id in crew_pairs(db, list(open_ends)):
        if mentor_id == actor.user_id:
            grant(trainee_id, shift_id, open_ends[shift_id])
    for ext in db.scalars(select(MentorAccessExtension).where(
            MentorAccessExtension.mentor_id == actor.user_id, MentorAccessExtension.revoked_at.is_(None),
            MentorAccessExtension.starts_at <= now, MentorAccessExtension.ends_at > now)):
        grant(ext.trainee_id, ext.shift_id, ext.ends_at)
    return grants


def mentor_grants(db: Session, actor: Actor, now: datetime) -> dict[uuid.UUID, set[uuid.UUID]]:
    """trainee_id -> shift ids the actor may currently mentor (windows + active extensions)."""
    return {t: set(shifts) for t, shifts in mentor_grant_ends(db, actor, now).items()}


@dataclass(frozen=True)
class JournalAccess:
    trainee_id: uuid.UUID
    is_self: bool = False
    is_admin: bool = False
    is_home_manager: bool = False
    mentor_shift_ids: frozenset = field(default_factory=frozenset)
    can_read_progress: bool = False

    @property
    def can_read_journal(self) -> bool:
        return self.is_self or self.is_admin or self.is_home_manager or bool(self.mentor_shift_ids)

    @property
    def can_read_all_summaries(self) -> bool:
        return self.is_self or self.is_admin or self.is_home_manager

    def can_read_summary(self, shift_id: uuid.UUID) -> bool:
        return self.can_read_all_summaries or shift_id in self.mentor_shift_ids

    @property
    def can_edit_description(self) -> bool:
        return self.is_self or self.is_admin

    def can_mentor_shift(self, shift_id: uuid.UUID) -> bool:
        return self.is_admin or shift_id in self.mentor_shift_ids

    @property
    def can_manage(self) -> bool:
        """Reopen mentor access, progress corrections, shared notes: current home-day manager or admin."""
        return self.is_admin or self.is_home_manager


def journal_access(db: Session, actor: Actor, trainee_id: uuid.UUID, now: datetime) -> JournalAccess:
    if not actor.is_active:
        return JournalAccess(trainee_id)
    home = home_weekday(db, trainee_id)
    is_home_manager = home is not None and home in actor.managed_weekdays
    mentor_shifts = frozenset(mentor_grants(db, actor, now).get(trainee_id, set()))
    is_self = actor.user_id == trainee_id
    can_progress = (is_self or actor.is_admin or actor.is_youth_manager or actor.is_day_manager
                    or bool(mentor_shifts))
    return JournalAccess(trainee_id, is_self=is_self, is_admin=actor.is_admin, is_home_manager=is_home_manager,
                         mentor_shift_ids=mentor_shifts, can_read_progress=can_progress)
