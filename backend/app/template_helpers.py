"""Jinja filters, globals and the role-aware navigation."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import g, request, url_for
from flask_login import current_user

from app.models.base import as_utc

NAV = [
    ("Overview", [
        ("Dashboard", "main.dashboard", "bi-grid-1x2", None),
    ]),
    ("Recruitment", [
        ("Hiring requests", "jobs.hiring_requests", "bi-clipboard-plus", "hiring_request.view"),
        ("Vacancies", "jobs.manage_list", "bi-briefcase", "job.view_internal"),
        ("Job clusters", "jobs.clusters", "bi-diagram-3", "clustering.view"),
        ("Candidates", "candidates.directory", "bi-people", "candidate.view"),
        ("Pipeline", "applications.pipeline", "bi-kanban", "application.view"),
        ("Interviews", "interviews.calendar", "bi-calendar-event", "interview.view"),
        ("Offers", "offers.list_offers", "bi-envelope-paper", "offer.view"),
    ]),
    ("People", [
        ("Employees", "employees.directory", "bi-person-badge", "employee.view"),
        ("Onboarding tasks", "onboarding.tasks", "bi-list-check", "onboarding.manage"),
        ("Templates", "onboarding.templates", "bi-layers", "onboarding.templates"),
        ("Training", "onboarding.training", "bi-mortarboard", "onboarding.manage"),
        ("IT & equipment", "onboarding.it_requests", "bi-laptop", "onboarding.manage"),
        ("Probation", "onboarding.probation", "bi-flag", "onboarding.manager_actions"),
    ]),
    ("Insights", [
        ("Analytics", "analytics.index", "bi-bar-chart-line", "analytics.view"),
    ]),
    ("Administration", [
        ("Users & roles", "admin.users", "bi-shield-lock", "admin.users"),
        ("Departments", "admin.departments", "bi-building", "department.manage"),
        ("AI service", "admin.ai_status", "bi-cpu", "admin.ai"),
        ("Audit log", "admin.audit", "bi-journal-text", "admin.audit"),
        ("Settings", "admin.settings", "bi-gear", "admin.settings"),
    ]),
]

CANDIDATE_NAV = [("My workspace", [
    ("Dashboard", "candidate.dashboard", "bi-grid-1x2", None),
    ("Browse jobs", "careers.list_jobs", "bi-search", None),
    ("My applications", "candidate.applications", "bi-send-check", None),
    ("Resumes", "candidate.resumes", "bi-file-earmark-person", None),
    ("Profile", "candidate.profile", "bi-person-gear", None),
])]

EMPLOYEE_NAV = [("My workspace", [
    ("My onboarding", "portal.onboarding", "bi-rocket-takeoff", None),
    ("My profile", "portal.profile", "bi-person-vcard", None),
    ("Browse jobs", "careers.list_jobs", "bi-search", None),
    ("My applications", "candidate.applications", "bi-send-check", None),
])]

BADGES = {
    # generic
    "draft": "secondary", "pending": "warning", "pending_approval": "warning", "published": "success",
    "paused": "warning", "closed": "dark", "archived": "secondary", "approved": "success", "rejected": "danger",
    "revision_requested": "warning",
    # applications
    "submitted": "info", "under_review": "primary", "on_hold": "warning", "further_review": "warning",
    "shortlisted": "teal", "interviewing": "indigo", "approved_for_offer": "teal", "offered": "indigo",
    "hired": "success", "withdrawn": "secondary", "offer_declined": "secondary",
    # interviews/offers
    "scheduled": "primary", "completed": "success", "cancelled": "secondary", "accepted": "success",
    "declined": "danger", "expired": "secondary",
    # onboarding
    "in_progress": "primary", "blocked": "danger", "requested": "warning", "verified": "success",
    "assigned": "warning", "fulfilled": "success", "missed": "danger",
    "pre_boarding": "info", "onboarding": "primary", "probation": "indigo", "active": "success", "exited": "secondary",
    "parsed": "success", "needs_ocr": "warning", "failed": "danger", "insufficient_data": "warning",
    "high": "danger", "medium": "warning", "low": "secondary",
}


def _tz():
    if "display_tz" not in g:
        from app.models import get_setting

        try:
            g.display_tz = ZoneInfo(get_setting("default_timezone") or "UTC")
        except Exception:
            g.display_tz = ZoneInfo("UTC")
    return g.display_tz


def fmt_dt(value, fmt="%d %b %Y, %H:%M", tz: str | None = None):
    if value is None:
        return "—"
    if isinstance(value, datetime):
        zone = ZoneInfo(tz) if tz else _tz()
        return as_utc(value).astimezone(zone).strftime(fmt)
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def fmt_date(value):
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return fmt_dt(value, "%d %b %Y")
    return value.strftime("%d %b %Y")


def humanize(value) -> str:
    return (str(value or "")).replace("_", " ").capitalize()


def owner(assignee_type) -> str:
    """Label for an onboarding owner group: HR / IT stay upper-case."""
    return {"hr": "HR", "it": "IT"}.get(assignee_type, humanize(assignee_type))


def badge(status) -> str:
    return BADGES.get(status, "secondary")


def money(value, currency=""):
    if value is None:
        return "—"
    return f"{currency} {value:,.2f}".strip()


def pct(value):
    return "—" if value is None else f"{value:.0f}%"


def register_template_helpers(app):
    app.jinja_env.filters.update(dt=fmt_dt, date=fmt_date, humanize=humanize, badge=badge, money=money, pct=pct, owner=owner)

    @app.context_processor
    def inject():
        nav = []
        unread = 0
        if current_user.is_authenticated:
            from app.services.notification_service import unread_count

            if current_user.is_staff:
                for section, items in NAV:
                    visible = [i for i in items if i[3] is None or current_user.can(i[3])]
                    if visible:
                        nav.append((section, visible))
            elif current_user.role == "employee":
                nav = EMPLOYEE_NAV
            else:
                nav = CANDIDATE_NAV
            unread = unread_count(current_user.id)
        return {"nav_sections": nav, "unread_notifications": unread, "app_name": app.config["APP_NAME"],
                "current_endpoint": request.endpoint or ""}

    app.jinja_env.globals.update(url_for=url_for)
