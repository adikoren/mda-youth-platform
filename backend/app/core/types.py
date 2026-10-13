import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import Mapped, mapped_column


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def tstz(nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def check_in(column: str, values: list[str] | tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


LEVELS = ("trainee", "youth", "mentor")
AUTHORITY_ROLES = ("admin", "youth_manager")
USER_STATUSES = ("pending", "active", "inactive")
DAY_KINDS = ("regular", "friday", "saturday", "holiday", "holiday_eve")
SPECIAL_DAY_KINDS = ("friday", "saturday", "holiday", "holiday_eve")
SHIFT_KINDS = ("morning", "reinforcement", "evening")
