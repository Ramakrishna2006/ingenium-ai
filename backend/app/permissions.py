"""Role definitions and the role -> permission matrix.

Permissions are defined in code (version-controlled and reviewable) rather than
in the database. Record-level *ownership/scope* rules live in ``app/access.py``.
Every protected route checks a permission here AND, where a specific record is
involved, a scope rule in ``access.py``.
"""
from __future__ import annotations

from functools import wraps

from flask import abort
from flask_login import current_user

SUPER_ADMIN = "super_admin"
HR_MANAGER = "hr_manager"
RECRUITER = "recruiter"
HIRING_MANAGER = "hiring_manager"
INTERVIEWER = "interviewer"
CANDIDATE = "candidate"
EMPLOYEE = "employee"

ROLES: tuple[str, ...] = (SUPER_ADMIN, HR_MANAGER, RECRUITER, HIRING_MANAGER, INTERVIEWER, CANDIDATE, EMPLOYEE)
STAFF_ROLES = frozenset({SUPER_ADMIN, HR_MANAGER, RECRUITER, HIRING_MANAGER, INTERVIEWER})

ROLE_LABELS = {
    SUPER_ADMIN: "Super Admin",
    HR_MANAGER: "HR Manager",
    RECRUITER: "Recruiter",
    HIRING_MANAGER: "Hiring Manager",
    INTERVIEWER: "Interviewer",
    CANDIDATE: "Candidate",
    EMPLOYEE: "Employee",
}

ALL_PERMISSIONS = frozenset({
    "admin.users", "admin.settings", "admin.audit", "admin.ai",
    "department.view", "department.manage",
    "hiring_request.view", "hiring_request.create", "hiring_request.decide",
    "job.view_internal", "job.manage",
    "candidate.view",               # scoped by access.py (all for HR, assigned for others)
    "application.view",             # scoped
    "application.review",           # status changes / shortlisting
    "application.screen",           # run & override screening
    "clustering.view", "clustering.run",
    "interview.view",               # scoped
    "interview.schedule",
    "interview.feedback",           # only for own assignments
    "decision.recommend", "decision.final",
    "offer.view", "offer.create", "offer.approve",
    "employee.view",                # scoped (HR: all, manager: own team)
    "employee.manage",
    "onboarding.manage", "onboarding.templates", "onboarding.manager_actions",
    "analytics.view",
    "self.candidate", "self.employee",
})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    SUPER_ADMIN: ALL_PERMISSIONS,
    HR_MANAGER: frozenset({
        "department.view", "department.manage",
        "hiring_request.view", "hiring_request.create", "hiring_request.decide",
        "job.view_internal", "job.manage",
        "candidate.view", "application.view", "application.review", "application.screen",
        "clustering.view", "clustering.run",
        "interview.view", "interview.schedule", "interview.feedback",
        "decision.recommend", "decision.final",
        "offer.view", "offer.create", "offer.approve",
        "employee.view", "employee.manage",
        "onboarding.manage", "onboarding.templates", "onboarding.manager_actions",
        "analytics.view", "admin.ai",
    }),
    RECRUITER: frozenset({
        "department.view",
        "hiring_request.view", "hiring_request.create",
        "job.view_internal", "job.manage",
        "candidate.view", "application.view", "application.review", "application.screen",
        "clustering.view", "clustering.run",
        "interview.view", "interview.schedule", "interview.feedback",
        "decision.recommend",
        "offer.view",
    }),
    HIRING_MANAGER: frozenset({
        "department.view",
        "hiring_request.view", "hiring_request.create",
        "job.view_internal",
        "candidate.view", "application.view", "application.screen",
        "clustering.view",
        "interview.view", "interview.feedback",
        "decision.recommend", "decision.final",
        "offer.view",
        "employee.view", "onboarding.manager_actions",
    }),
    INTERVIEWER: frozenset({"interview.view", "interview.feedback"}),
    CANDIDATE: frozenset({"self.candidate"}),
    EMPLOYEE: frozenset({"self.employee", "self.candidate"}),
}


def role_has(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def permission_required(*permissions: str):
    """Require an authenticated, active user holding ALL listed permissions."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            for perm in permissions:
                if not current_user.can(perm):
                    abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def any_permission_required(*permissions: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not any(current_user.can(p) for p in permissions):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
