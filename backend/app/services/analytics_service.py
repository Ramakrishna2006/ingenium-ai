"""Recruitment & onboarding metrics. Values are None when there is not enough data — never invented."""
from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta, timezone

import sqlalchemy as sa

from app.extensions import db
from app.models import (
    Application, ApplicationEvent, Department, Employee, Interview, InterviewFeedback, Job, Offer,
    OnboardingTask, today_utc,
)
from app.models.base import as_utc
from app.services.job_clustering import is_stale, latest_run

FUNNEL_STAGES = [
    ("submitted", "Applied", {"submitted"}),
    ("reviewed", "Reviewed", {"under_review", "on_hold", "further_review", "shortlisted", "rejected"}),
    ("shortlisted", "Shortlisted", {"shortlisted"}),
    ("interviewing", "Interviewed", {"interviewing"}),
    ("approved", "Approved for offer", {"approved_for_offer"}),
    ("offered", "Offered", {"offered"}),
    ("hired", "Hired", {"hired"}),
]

DEFINITIONS = {
    "applications": "Applications submitted within the selected date range.",
    "funnel": "Applications (submitted in range) that ever reached each stage, based on status history.",
    "conversion": "Share of applications in range that reached a stage, relative to all applications in range.",
    "offer_acceptance": "Accepted ÷ (accepted + declined + expired) for offers extended in range. Pending offers are excluded.",
    "time_to_hire": "Days from application submission to offer acceptance, for hires completed in range. Shown only when at least one hire exists.",
    "onboarding_completion": "Completed onboarding tasks ÷ all onboarding tasks for employees joining in range.",
    "overdue_tasks": "Open onboarding tasks whose due date is before today (all employees).",
}


def _bounds(start: date, end: date):
    s = datetime.combine(start, time.min, tzinfo=timezone.utc)
    e = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return s, e


def default_range(days: int = 90):
    end = today_utc()
    return end - timedelta(days=days - 1), end


def compute_metrics(start: date | None = None, end: date | None = None, department_id: int | None = None) -> dict:
    if start is None or end is None:
        start, end = default_range()
    if end < start:
        start, end = end, start
    s, e = _bounds(start, end)

    app_filter = [Application.submitted_at >= s, Application.submitted_at < e]
    if department_id:
        app_filter.append(Application.job_id.in_(sa.select(Job.id).where(Job.department_id == department_id)))
    app_ids_q = sa.select(Application.id).where(*app_filter)
    total_apps = db.session.scalar(sa.select(sa.func.count()).select_from(app_ids_q.subquery())) or 0

    by_status = dict(db.session.execute(
        sa.select(Application.status, sa.func.count(Application.id)).where(*app_filter).group_by(Application.status)).all())

    # Daily volume
    rows = db.session.execute(sa.select(Application.submitted_at).where(*app_filter)).scalars().all()
    daily: dict[str, int] = {}
    for ts in rows:
        key = as_utc(ts).date().isoformat()
        daily[key] = daily.get(key, 0) + 1
    span = (end - start).days + 1
    volume = [{"date": (start + timedelta(days=i)).isoformat(),
               "count": daily.get((start + timedelta(days=i)).isoformat(), 0)} for i in range(span)] if span <= 400 else []

    # Funnel from status history
    reached = db.session.execute(
        sa.select(ApplicationEvent.to_status, sa.func.count(sa.distinct(ApplicationEvent.application_id)))
        .where(ApplicationEvent.application_id.in_(app_ids_q)).group_by(ApplicationEvent.to_status)).all()
    reached_map = dict(reached)
    funnel = []
    for key, label, statuses in FUNNEL_STAGES:
        if key == "submitted":
            count = total_apps
        elif key == "reviewed":
            count = db.session.scalar(sa.select(sa.func.count(sa.distinct(ApplicationEvent.application_id))).where(
                ApplicationEvent.application_id.in_(app_ids_q), ApplicationEvent.to_status.in_(statuses))) or 0
        else:
            count = sum(reached_map.get(st, 0) for st in statuses)
        funnel.append({"stage": key, "label": label, "count": count,
                       "conversion": round(100 * count / total_apps, 1) if total_apps else None})

    # Vacancies by department
    dept_rows = db.session.execute(
        sa.select(Department.name, Job.status, sa.func.count(Job.id)).join(Job, Job.department_id == Department.id)
        .group_by(Department.name, Job.status)).all()
    vacancies: dict[str, dict] = {}
    for name, status, count in dept_rows:
        vacancies.setdefault(name, {"department": name, "published": 0, "total": 0})
        vacancies[name]["total"] += count
        if status == "published":
            vacancies[name]["published"] += count

    # Interviews
    iv_filter = [Interview.scheduled_at >= s, Interview.scheduled_at < e]
    interview_status = dict(db.session.execute(
        sa.select(Interview.status, sa.func.count(Interview.id)).where(*iv_filter).group_by(Interview.status)).all())
    recommendations = dict(db.session.execute(
        sa.select(InterviewFeedback.recommendation, sa.func.count(InterviewFeedback.id))
        .join(Interview).where(*iv_filter).group_by(InterviewFeedback.recommendation)).all())

    # Offers
    offer_rows = dict(db.session.execute(
        sa.select(Offer.status, sa.func.count(Offer.id)).where(Offer.extended_at >= s, Offer.extended_at < e)
        .group_by(Offer.status)).all())
    decided = sum(offer_rows.get(k, 0) for k in ("accepted", "declined", "expired"))
    acceptance = round(100 * offer_rows.get("accepted", 0) / decided, 1) if decided else None

    # Time to hire
    hired_events = db.session.execute(
        sa.select(Application.submitted_at, ApplicationEvent.created_at)
        .join(ApplicationEvent, ApplicationEvent.application_id == Application.id)
        .where(ApplicationEvent.to_status == "hired", ApplicationEvent.created_at >= s, ApplicationEvent.created_at < e)).all()
    durations = [(as_utc(h) - as_utc(sub)).total_seconds() / 86400 for sub, h in hired_events]
    time_to_hire = None
    if durations:
        time_to_hire = {"hires": len(durations), "median_days": round(statistics.median(durations), 1),
                        "average_days": round(sum(durations) / len(durations), 1)}

    # Onboarding
    emp_ids = sa.select(Employee.id).where(Employee.joining_date >= start, Employee.joining_date <= end + timedelta(days=60))
    task_counts = dict(db.session.execute(
        sa.select(OnboardingTask.status, sa.func.count(OnboardingTask.id))
        .where(OnboardingTask.employee_id.in_(emp_ids)).group_by(OnboardingTask.status)).all())
    total_tasks = sum(task_counts.values())
    overdue = db.session.scalar(sa.select(sa.func.count(OnboardingTask.id)).where(
        OnboardingTask.status != "completed", OnboardingTask.due_date < today_utc())) or 0
    employees_by_status = dict(db.session.execute(
        sa.select(Employee.employment_status, sa.func.count(Employee.id)).group_by(Employee.employment_status)).all())

    run = latest_run()
    clusters = None
    if run is not None:
        clusters = {"run_id": run.id, "status": run.status, "silhouette": run.silhouette, "stale": is_stale(run),
                    "groups": [{"label": c["label"], "size": c["size"]} for c in run.clusters or []]}

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat(), "department_id": department_id},
        "applications": {"total": total_apps, "by_status": by_status, "daily": volume},
        "funnel": funnel,
        "vacancies_by_department": sorted(vacancies.values(), key=lambda v: -v["total"]),
        "open_vacancies": db.session.scalar(sa.select(sa.func.count(Job.id)).where(Job.status == "published")) or 0,
        "interviews": {"by_status": interview_status, "recommendations": recommendations},
        "offers": {"by_status": offer_rows, "decided": decided, "acceptance_rate": acceptance},
        "time_to_hire": time_to_hire,
        "onboarding": {
            "tasks_by_status": task_counts, "total_tasks": total_tasks,
            "completion_rate": round(100 * task_counts.get("completed", 0) / total_tasks, 1) if total_tasks else None,
            "overdue_tasks": overdue, "employees_by_status": employees_by_status,
        },
        "clusters": clusters,
        "definitions": DEFINITIONS,
    }
