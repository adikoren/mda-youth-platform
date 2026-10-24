"""The one rule that makes a volunteer-cancelled assignment disappear from every live read of a published
version. Kept in its own module (models only) so access, mentoring, notifications and scheduling can all
import it without circular imports."""
import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.scheduling.models import Assignment, AssignmentCancellation


def live_assignment(a=Assignment):
    """SQL condition: the assignment has not been cancelled by its volunteer."""
    return ~exists().where(
        AssignmentCancellation.version_id == a.version_id,
        AssignmentCancellation.shift_id == a.shift_id,
        AssignmentCancellation.user_id == a.user_id,
    )


def live_boarding(b):
    """SQL condition: the boarding entry (סדר עלייה) has not been cancelled by its volunteer."""
    return ~exists().where(
        AssignmentCancellation.version_id == b.version_id,
        AssignmentCancellation.shift_id == b.shift_id,
        AssignmentCancellation.user_id == b.user_id,
    )


def is_cancelled(db: Session, version_id: uuid.UUID, shift_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return db.scalar(select(AssignmentCancellation.id).where(
        AssignmentCancellation.version_id == version_id, AssignmentCancellation.shift_id == shift_id,
        AssignmentCancellation.user_id == user_id)) is not None
