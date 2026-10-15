"""Pydantic input schemas shared by HTML form handlers and the JSON API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import re

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Annotated

from app.models.employees import (
    ASSIGNEE_TYPES, IT_REQUEST_STATUSES, IT_REQUEST_TYPES, MILESTONE_KINDS, PRIORITIES,
    PROBATION_OUTCOMES, TASK_STATUSES, TEMPLATE_ITEM_CATEGORIES, TRAINING_STATUSES,
)
from app.models.hiring import DECISIONS, INTERVIEW_KINDS, RATING_CRITERIA, RECOMMENDATIONS
from app.models.recruitment import EMPLOYMENT_TYPES, JOB_STATUSES, MANUAL_REVIEW_STATUSES
from app.permissions import ROLES
from app.services.skills import normalize_skill_list


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def _email(v: str) -> str:
    v = (v or "").strip().lower()
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address.")
    return v


EmailStr = Annotated[str, AfterValidator(_email)]


class Schema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


def _blank_to_none(v):
    return None if isinstance(v, str) and not v.strip() else v


def _skills(v):
    return normalize_skill_list(v or [])


def _textlist(v):
    if isinstance(v, str):
        v = [line for line in v.replace(";", "\n").split("\n")]
    return [str(x).strip()[:200] for x in (v or []) if str(x).strip()][:30]


# ------------------------------------------------------------------ auth/users
class RegisterIn(Schema):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    confirm_password: str | None = None
    consent: bool = False

    @field_validator("email")
    @classmethod
    def _lower(cls, v):
        return v.lower()

    @model_validator(mode="after")
    def _check(self):
        if self.confirm_password is not None and self.confirm_password != self.password:
            raise ValueError("Passwords do not match.")
        if not self.consent:
            raise ValueError("You must accept the privacy notice to register.")
        return self


class LoginIn(Schema):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    remember: bool = False


class UserCreateIn(Schema):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    role: Literal[ROLES]  # type: ignore[valid-type]
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v):
        return v.lower()


class UserUpdateIn(Schema):
    role: Literal[ROLES] | None = None  # type: ignore[valid-type]
    active: bool | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=150)


# ---------------------------------------------------------------- org structure
class DepartmentIn(Schema):
    name: str = Field(min_length=2, max_length=120)
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=120)
    head_user_id: int | None = None
    is_active: bool = True

    _n = field_validator("description", "location", "head_user_id", mode="before")(_blank_to_none)

    @field_validator("code")
    @classmethod
    def _upper(cls, v):
        return v.upper()


class HiringRequestIn(Schema):
    title: str = Field(min_length=2, max_length=150)
    department_id: int
    hiring_manager_id: int | None = None
    headcount: int = Field(default=1, ge=1, le=500)
    employment_type: Literal[EMPLOYMENT_TYPES] = "full_time"  # type: ignore[valid-type]
    justification: str = Field(min_length=10, max_length=4000)
    target_date: date | None = None
    budget_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    budget_currency: str | None = Field(default=None, min_length=3, max_length=3)

    _n = field_validator("hiring_manager_id", "target_date", "budget_amount", "budget_currency", mode="before")(_blank_to_none)


class HiringRequestDecisionIn(Schema):
    decision: Literal["approved", "rejected", "revision_requested"]
    notes: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------------ jobs
class JobIn(Schema):
    title: str = Field(min_length=2, max_length=150)
    department_id: int
    hiring_request_id: int | None = None
    description: str = Field(min_length=20, max_length=20000)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    qualifications: str | None = Field(default=None, max_length=4000)
    experience_min_years: int | None = Field(default=None, ge=0, le=50)
    experience_max_years: int | None = Field(default=None, ge=0, le=60)
    location: str | None = Field(default=None, max_length=120)
    is_remote: bool = False
    employment_type: Literal[EMPLOYMENT_TYPES] = "full_time"  # type: ignore[valid-type]
    headcount: int = Field(default=1, ge=1, le=500)
    application_deadline: date | None = None
    recruiter_id: int | None = None
    hiring_manager_id: int | None = None

    _n = field_validator("hiring_request_id", "qualifications", "experience_min_years", "experience_max_years",
                         "location", "application_deadline", "recruiter_id", "hiring_manager_id",
                         mode="before")(_blank_to_none)
    _s = field_validator("required_skills", "preferred_skills", mode="before")(_skills)

    @model_validator(mode="after")
    def _range(self):
        if (self.experience_min_years is not None and self.experience_max_years is not None
                and self.experience_max_years < self.experience_min_years):
            raise ValueError("Maximum experience cannot be lower than minimum experience.")
        return self


class JobStatusIn(Schema):
    status: Literal[JOB_STATUSES]  # type: ignore[valid-type]


class JobDraftIn(Schema):
    title: str = Field(min_length=2, max_length=150)
    department: str = Field(default="", max_length=120)
    skills: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)

    _s = field_validator("skills", mode="before")(_skills)


# ------------------------------------------------------------ candidates/apps
class CandidateProfileIn(Schema):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, max_length=40, pattern=r"^[0-9+()\-\s]{6,40}$")
    location: str | None = Field(default=None, max_length=120)
    headline: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    years_experience: Decimal | None = Field(default=None, ge=0, le=60, max_digits=4, decimal_places=1)
    skills: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    linkedin_url: str | None = Field(default=None, max_length=255, pattern=r"^https?://")
    portfolio_url: str | None = Field(default=None, max_length=255, pattern=r"^https?://")

    _n = field_validator("full_name", "phone", "location", "headline", "summary", "years_experience",
                         "linkedin_url", "portfolio_url", mode="before")(_blank_to_none)
    _s = field_validator("skills", mode="before")(_skills)
    _t = field_validator("education", "certifications", mode="before")(_textlist)


class ApplyIn(Schema):
    job_id: int
    resume_id: int | None = None
    cover_letter: str | None = Field(default=None, max_length=5000)

    _n = field_validator("resume_id", "cover_letter", mode="before")(_blank_to_none)


class ApplicationStatusIn(Schema):
    status: Literal[MANUAL_REVIEW_STATUSES]  # type: ignore[valid-type]
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reason_required(self):
        if self.status in ("rejected", "on_hold") and not self.reason:
            raise ValueError("A reason is required for this decision.")
        return self


class ScreeningOverrideIn(Schema):
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reviewer_notes: str = Field(min_length=3, max_length=3000)

    _s = field_validator("matched_skills", "missing_skills", mode="before")(_skills)


class DecisionIn(Schema):
    decision: Literal[DECISIONS]  # type: ignore[valid-type]
    rationale: str = Field(min_length=10, max_length=4000)


class CompareJobsIn(Schema):
    job_ids: list[int] = Field(default_factory=list, max_length=10)
    explain_with_ai: bool = False


# ------------------------------------------------------------------ interviews
def _zone(v):
    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError("Unknown time zone.")
    return v


class InterviewIn(Schema):
    kind: Literal[INTERVIEW_KINDS] = "technical"  # type: ignore[valid-type]
    scheduled_local: datetime  # naive local time in ``timezone``
    timezone: str = Field(default="UTC", max_length=64)
    duration_minutes: int = Field(default=45, ge=10, le=480)
    location: str | None = Field(default=None, max_length=255)
    meeting_link: str | None = Field(default=None, max_length=500, pattern=r"^https://")
    interviewer_ids: list[int] = Field(min_length=1, max_length=10)
    notes: str | None = Field(default=None, max_length=4000)

    _n = field_validator("location", "meeting_link", "notes", mode="before")(_blank_to_none)
    _z = field_validator("timezone")(_zone)

    @model_validator(mode="after")
    def _where(self):
        if not self.location and not self.meeting_link:
            raise ValueError("Provide a location or a meeting link.")
        return self


class RescheduleIn(Schema):
    scheduled_local: datetime
    timezone: str = Field(default="UTC", max_length=64)
    reason: str | None = Field(default=None, max_length=500)

    _z = field_validator("timezone")(_zone)


class InterviewStatusIn(Schema):
    status: Literal["completed", "cancelled"]
    reason: str | None = Field(default=None, max_length=255)


class FeedbackIn(Schema):
    ratings: dict[str, int] = Field(default_factory=dict)
    overall_rating: int = Field(ge=1, le=5)
    recommendation: Literal[RECOMMENDATIONS]  # type: ignore[valid-type]
    strengths: str | None = Field(default=None, max_length=4000)
    concerns: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("ratings")
    @classmethod
    def _ratings(cls, v):
        clean = {}
        for key, value in (v or {}).items():
            if key not in RATING_CRITERIA:
                continue
            if not 1 <= int(value) <= 5:
                raise ValueError("Ratings must be between 1 and 5.")
            clean[key] = int(value)
        return clean


# ---------------------------------------------------------------------- offers
class OfferIn(Schema):
    designation: str = Field(min_length=2, max_length=150)
    department_id: int
    salary: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    joining_date: date
    expires_on: date | None = None
    terms: str | None = Field(default=None, max_length=10000)

    _n = field_validator("expires_on", "terms", mode="before")(_blank_to_none)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v):
        return v.upper()


class OfferDecisionIn(Schema):
    decision: Literal["approved", "revision_requested"]
    comments: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _needs_comment(self):
        if self.decision == "revision_requested" and not self.comments:
            raise ValueError("Explain what needs to be revised.")
        return self


class OfferResponseIn(Schema):
    response: Literal["accept", "decline"]
    reason: str | None = Field(default=None, max_length=2000)


# ------------------------------------------------------------------ onboarding
class TemplateIn(Schema):
    name: str = Field(min_length=2, max_length=150)
    department_id: int | None = None
    role_keyword: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    is_default: bool = False
    is_active: bool = True

    _n = field_validator("department_id", "role_keyword", "description", mode="before")(_blank_to_none)


class TemplateItemIn(Schema):
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: Literal[TEMPLATE_ITEM_CATEGORIES] = "orientation"  # type: ignore[valid-type]
    assignee_type: Literal[ASSIGNEE_TYPES] = "employee"  # type: ignore[valid-type]
    due_offset_days: int = Field(default=0, ge=-60, le=365)
    priority: Literal[PRIORITIES] = "medium"  # type: ignore[valid-type]
    doc_type: str | None = Field(default=None, max_length=60, pattern=r"^[a-z0-9_]+$")

    _n = field_validator("description", "doc_type", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _doc(self):
        if self.category == "document" and not self.doc_type:
            raise ValueError("Document items need a document type key (e.g. id_proof).")
        return self


class TaskIn(Schema):
    employee_id: int
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: Literal[TEMPLATE_ITEM_CATEGORIES] = "hr"  # type: ignore[valid-type]
    assignee_type: Literal[ASSIGNEE_TYPES] = "employee"  # type: ignore[valid-type]
    assignee_user_id: int | None = None
    due_date: date | None = None
    priority: Literal[PRIORITIES] = "medium"  # type: ignore[valid-type]

    _n = field_validator("description", "assignee_user_id", "due_date", mode="before")(_blank_to_none)


class TaskStatusIn(Schema):
    status: Literal[TASK_STATUSES]  # type: ignore[valid-type]
    blocked_reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _reason(self):
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("Explain why the task is blocked.")
        return self


class TrainingIn(Schema):
    employee_id: int
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    resource_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    due_date: date | None = None
    is_mandatory: bool = True

    _n = field_validator("description", "resource_url", "due_date", mode="before")(_blank_to_none)


class TrainingStatusIn(Schema):
    status: Literal[TRAINING_STATUSES]  # type: ignore[valid-type]


class ITRequestIn(Schema):
    employee_id: int
    request_type: Literal[IT_REQUEST_TYPES]  # type: ignore[valid-type]
    details: str = Field(min_length=3, max_length=500)


class ITRequestUpdateIn(Schema):
    status: Literal[IT_REQUEST_STATUSES]  # type: ignore[valid-type]
    notes: str | None = Field(default=None, max_length=500)


class DocumentReviewIn(Schema):
    decision: Literal["verified", "rejected"]
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _notes(self):
        if self.decision == "rejected" and not self.notes:
            raise ValueError("Explain why the document was rejected.")
        return self


class MilestoneIn(Schema):
    employee_id: int
    kind: Literal[MILESTONE_KINDS] = "check_in"  # type: ignore[valid-type]
    title: str = Field(min_length=2, max_length=150)
    due_date: date
    goals: str | None = Field(default=None, max_length=4000)

    _n = field_validator("goals", mode="before")(_blank_to_none)


class MilestoneCompleteIn(Schema):
    feedback: str = Field(min_length=5, max_length=4000)
    rating: int | None = Field(default=None, ge=1, le=5)
    goals: str | None = Field(default=None, max_length=4000)
    outcome: Literal[PROBATION_OUTCOMES] | None = None  # type: ignore[valid-type]

    _n = field_validator("rating", "goals", "outcome", mode="before")(_blank_to_none)


class EmployeeAssignIn(Schema):
    department_id: int
    manager_id: int | None = None
    designation: str = Field(min_length=2, max_length=150)
    work_location: str | None = Field(default=None, max_length=120)
    joining_date: date

    _n = field_validator("manager_id", "work_location", mode="before")(_blank_to_none)


# --------------------------------------------------------------------- settings
class SettingsIn(Schema):
    org_name: str = Field(min_length=2, max_length=150)
    default_timezone: str = Field(max_length=64)
    default_currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    probation_days: int = Field(ge=0, le=730)
    offer_validity_days: int = Field(ge=1, le=90)
    resume_retention_days: int = Field(ge=30, le=3650)
    allow_application_withdrawal: bool = False

    _z = field_validator("default_timezone")(_zone)

    @field_validator("default_currency")
    @classmethod
    def _upper(cls, v):
        return v.upper()


class ModelConfigIn(Schema):
    ollama_model_override: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9._:/-]*$")


def form_to_dict(form, list_fields: tuple[str, ...] = (), bool_fields: tuple[str, ...] = ()) -> dict:
    """Convert an HTML form MultiDict into a plain dict for schema validation."""
    data = {}
    for key in form.keys():
        if key == "csrf_token":
            continue
        if key in list_fields:
            values = form.getlist(key)
            data[key] = values if len(values) > 1 or key.endswith("_ids") else (values[0] if values else "")
        else:
            data[key] = form.get(key)
    for key in bool_fields:
        data[key] = form.get(key) in ("on", "true", "1", "yes")
    return data
