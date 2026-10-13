"""Import every model so metadata is complete (Alembic, tests)."""
from app.accounts.models import AuthorityRole, AuthSession, DayMembership, ManagerDayScope, User, VolunteerLevel  # noqa: F401
from app.audit.models import AuditEvent  # noqa: F401
from app.core.db import Base  # noqa: F401
from app.days.models import ActivityDay, RegistrationTemplate, Shift, Station  # noqa: F401
from app.notifications.models import OutboxMessage  # noqa: F401
from app.registration.models import (  # noqa: F401
    AvailabilityAttention,
    Invitation,
    ManagerAddedParticipant,
    QueueOrder,
    Registration,
    RegistrationChoice,
)
from app.scheduling.models import AmbulanceSlot, Assignment, ScheduleVersion  # noqa: F401
from app.mentoring.models import (  # noqa: F401
    MentorAccessExtension,
    MentoringSummary,
    PersonalNote,
    ProgressCorrection,
    Ride,
    RideAssessment,
    RideContributor,
)
