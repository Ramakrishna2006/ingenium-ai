"""Import every model so SQLAlchemy/Alembic see the full metadata."""
from app.models.base import as_utc, today_utc, utcnow  # noqa: F401
from app.models.employees import (  # noqa: F401
    Employee, EmployeeDocument, ITAccessRequest, OnboardingTask, OnboardingTemplate,
    OnboardingTemplateItem, ProbationMilestone, TrainingAssignment,
)
from app.models.hiring import (  # noqa: F401
    HiringDecision, Interview, InterviewerAssignment, InterviewFeedback, Offer, OfferApproval,
)
from app.models.organization import Department, OrgSetting, get_setting, set_setting  # noqa: F401
from app.models.recruitment import (  # noqa: F401
    Application, ApplicationEvent, Candidate, HiringRequest, Job, JobClusterAssignment, JobClusterRun,
    Resume, ScreeningResult,
)
from app.models.system import AuditLog, Notification  # noqa: F401
from app.models.user import User  # noqa: F401
