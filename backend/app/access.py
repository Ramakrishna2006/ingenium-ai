"""Record-level access rules (ownership & assignment scopes).

Role permissions (app/permissions.py) answer "may this role do X at all?".
These helpers answer "may this user do X to *this* record?" and build
scoped queries so list pages never leak records outside the user's scope.
"""
from __future__ import annotations

import sqlalchemy as sa

from app.errors import Forbidden, NotFound
from app.extensions import db
from app.models import (
    Application, Candidate, Employee, Interview, InterviewerAssignment, Job, Offer,
)
from app.permissions import EMPLOYEE, HIRING_MANAGER, HR_MANAGER, INTERVIEWER, RECRUITER, SUPER_ADMIN

ORG_WIDE = frozenset({SUPER_ADMIN, HR_MANAGER})
CANDIDATE_VISIBLE_OFFER_STATUSES = ("approved", "accepted", "declined", "expired")


def get_or_404(model, ident, message: str | None = None):
    obj = db.session.get(model, ident)
    if obj is None:
        raise NotFound(message or f"{model.__name__} not found.")
    return obj


def is_org_wide(user) -> bool:
    return user.is_authenticated and user.role in ORG_WIDE


# ---------------------------------------------------------------- jobs
def can_manage_job(user, job: Job) -> bool:
    if not user.can("job.manage"):
        return False
    if is_org_wide(user):
        return True
    return user.role == RECRUITER and user.id in (job.recruiter_id, job.created_by_id)


def _assigned_job_ids(user):
    """Subquery of job ids the user is assigned to via recruiting or hiring-manager role."""
    if user.role == RECRUITER:
        return sa.select(Job.id).where(sa.or_(Job.recruiter_id == user.id, Job.created_by_id == user.id))
    if user.role == HIRING_MANAGER:
        return sa.select(Job.id).where(Job.hiring_manager_id == user.id)
    return None


def _interviewer_application_ids(user):
    return (
        sa.select(Interview.application_id)
        .join(InterviewerAssignment, InterviewerAssignment.interview_id == Interview.id)
        .where(InterviewerAssignment.interviewer_id == user.id)
    )


# -------------------------------------------------------- applications
def application_scope(user, stmt=None):
    """Restrict a select(Application) statement to what ``user`` may see."""
    stmt = stmt if stmt is not None else sa.select(Application)
    if is_org_wide(user):
        return stmt
    conditions = []
    job_ids = _assigned_job_ids(user)
    if job_ids is not None:
        conditions.append(Application.job_id.in_(job_ids))
    if user.can("interview.feedback"):
        conditions.append(Application.id.in_(_interviewer_application_ids(user)))
    if user.candidate is not None and user.can("self.candidate"):
        conditions.append(Application.candidate_id == user.candidate.id)
    if not conditions:
        return stmt.where(sa.false())
    return stmt.where(sa.or_(*conditions))


def can_view_application(user, application: Application) -> bool:
    if not user.is_authenticated:
        return False
    if is_org_wide(user):
        return True
    if user.candidate is not None and application.candidate_id == user.candidate.id:
        return True
    job = application.job
    if user.role == RECRUITER and user.id in (job.recruiter_id, job.created_by_id):
        return True
    if user.role == HIRING_MANAGER and job.hiring_manager_id == user.id:
        return True
    return any(user.id in iv.interviewer_ids for iv in application.interviews)


def is_application_owner(user, application: Application) -> bool:
    return user.candidate is not None and application.candidate_id == user.candidate.id


def can_review_application(user, application: Application) -> bool:
    """Shortlisting/status changes: HR org-wide, recruiters on their jobs."""
    if not user.can("application.review"):
        return False
    if is_org_wide(user):
        return True
    job = application.job
    return user.role == RECRUITER and user.id in (job.recruiter_id, job.created_by_id)


def can_screen_application(user, application: Application) -> bool:
    if not user.can("application.screen"):
        return False
    if is_org_wide(user):
        return True
    job = application.job
    return (user.role == RECRUITER and user.id in (job.recruiter_id, job.created_by_id)) or (
        user.role == HIRING_MANAGER and job.hiring_manager_id == user.id
    )


def can_make_final_decision(user, application: Application) -> bool:
    if not user.can("decision.final"):
        return False
    return is_org_wide(user) or application.job.hiring_manager_id == user.id


def can_recommend(user, application: Application) -> bool:
    return user.can("decision.recommend") and (can_screen_application(user, application) or is_org_wide(user))


def is_limited_viewer(user, application: Application) -> bool:
    """Interviewer-only access: hide contact details and other non-essential personal data."""
    return not (is_org_wide(user) or can_screen_application(user, application) or is_application_owner(user, application))


def require(condition: bool, message: str = "You do not have permission for this action."):
    if not condition:
        raise Forbidden(message)


# ------------------------------------------------------------ candidates
def can_view_candidate(user, candidate: Candidate) -> bool:
    if not user.is_authenticated:
        return False
    if user.candidate is not None and user.candidate.id == candidate.id:
        return True
    if not user.can("candidate.view"):
        return False
    if is_org_wide(user):
        return True
    stmt = application_scope(user, sa.select(sa.func.count(Application.id))).where(
        Application.candidate_id == candidate.id
    )
    return db.session.scalar(stmt) > 0


def candidate_scope(user, stmt=None):
    stmt = stmt if stmt is not None else sa.select(Candidate)
    if is_org_wide(user):
        return stmt
    visible_apps = application_scope(user, sa.select(Application.candidate_id))
    return stmt.where(Candidate.id.in_(visible_apps))


# ------------------------------------------------------------ interviews
def interview_scope(user, stmt=None):
    stmt = stmt if stmt is not None else sa.select(Interview)
    if is_org_wide(user):
        return stmt
    conditions = [Interview.id.in_(
        sa.select(InterviewerAssignment.interview_id).where(InterviewerAssignment.interviewer_id == user.id)
    )]
    job_ids = _assigned_job_ids(user)
    if job_ids is not None:
        conditions.append(Interview.application_id.in_(sa.select(Application.id).where(Application.job_id.in_(job_ids))))
    return stmt.where(sa.or_(*conditions))


def can_view_interview(user, interview: Interview) -> bool:
    if is_org_wide(user) or user.id in interview.interviewer_ids:
        return True
    job = interview.application.job
    return (user.role == RECRUITER and user.id in (job.recruiter_id, job.created_by_id)) or (
        user.role == HIRING_MANAGER and job.hiring_manager_id == user.id
    )


def can_schedule_interview(user, application: Application) -> bool:
    return user.can("interview.schedule") and can_review_application(user, application)


def can_view_all_feedback(user, interview: Interview) -> bool:
    """Independent feedback: assigned interviewers only see peers' feedback after submitting their own."""
    if user.role in ORG_WIDE:
        return True
    job = interview.application.job
    if (user.role == RECRUITER and user.id in (job.recruiter_id, job.created_by_id)) or (
        user.role == HIRING_MANAGER and job.hiring_manager_id == user.id
    ):
        return user.id not in interview.interviewer_ids or any(f.interviewer_id == user.id for f in interview.feedback)
    return any(f.interviewer_id == user.id for f in interview.feedback)


# ---------------------------------------------------------------- offers
def can_view_offer(user, offer: Offer) -> bool:
    if is_application_owner(user, offer.application):
        return offer.status in CANDIDATE_VISIBLE_OFFER_STATUSES
    if not user.can("offer.view"):
        return False
    return is_org_wide(user) or can_screen_application(user, offer.application)


def offer_scope(user, stmt=None):
    stmt = stmt if stmt is not None else sa.select(Offer)
    if is_org_wide(user):
        return stmt
    visible = application_scope(user, sa.select(Application.id))
    stmt = stmt.where(Offer.application_id.in_(visible))
    if not user.can("offer.view"):
        stmt = stmt.where(Offer.status.in_(CANDIDATE_VISIBLE_OFFER_STATUSES))
    return stmt


# ------------------------------------------------------------- employees
def can_view_employee(user, employee: Employee) -> bool:
    if employee.user_id == user.id:
        return True
    if not user.can("employee.view"):
        return False
    return is_org_wide(user) or employee.manager_id == user.id


def employee_scope(user, stmt=None):
    stmt = stmt if stmt is not None else sa.select(Employee)
    if is_org_wide(user):
        return stmt
    if user.can("employee.view"):
        return stmt.where(sa.or_(Employee.manager_id == user.id, Employee.user_id == user.id))
    return stmt.where(Employee.user_id == user.id)


def can_manage_employee(user, employee: Employee) -> bool:
    return user.can("employee.manage") and is_org_wide(user)


def is_manager_of(user, employee: Employee) -> bool:
    return employee.manager_id == user.id or is_org_wide(user)


def current_employee(user) -> Employee | None:
    if not user.is_authenticated:
        return None
    return db.session.scalar(sa.select(Employee).where(Employee.user_id == user.id))


__all__ = [n for n in dir() if not n.startswith("_")] + ["EMPLOYEE", "INTERVIEWER"]
