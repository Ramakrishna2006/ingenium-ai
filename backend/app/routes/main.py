from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import access
from app.extensions import db
from app.models import (
    Application, Employee, Interview, InterviewerAssignment, InterviewFeedback, Job, Notification, Offer,
    OnboardingTask, get_setting, today_utc, utcnow,
)
from app.routes._helpers import paginate
from app.services.notification_service import mark_read

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    jobs = db.session.scalars(sa.select(Job).where(Job.status == "published").order_by(Job.published_at.desc()).limit(6)).all()
    open_count = db.session.scalar(sa.select(sa.func.count(Job.id)).where(Job.status == "published")) or 0
    return render_template("public/index.html", jobs=jobs, open_count=open_count, org_name=get_setting("org_name"))


@bp.get("/apply")
def apply_shortcut():
    """Short, shareable link for job seekers."""
    return redirect(url_for("careers.apply_home"))


@bp.get("/employee")
def employee_shortcut():
    """Short link for employees and staff; signed-in users go straight to their workspace."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login", **{"as": "employee"}))


@bp.get("/privacy")
def privacy():
    return render_template("public/privacy.html", org_name=get_setting("org_name"),
                           retention_days=get_setting("resume_retention_days"))


@bp.get("/health")
def health():
    """Liveness: the process is up. Does not touch the database."""
    return jsonify(status="ok", service="ingenium-ai")


@bp.get("/ready")
def ready():
    """Readiness: database reachable. AI availability is reported but not required."""
    checks = {"database": "ok"}
    status = 200
    try:
        db.session.execute(sa.text("SELECT 1"))
    except Exception:
        checks["database"] = "unavailable"
        status = 503
    return jsonify(status="ok" if status == 200 else "degraded", checks=checks), status


@bp.get("/dashboard")
@login_required
def dashboard():
    if current_user.role == "candidate":
        return redirect(url_for("candidate.dashboard"))
    if current_user.role == "employee":
        return redirect(url_for("portal.onboarding"))
    now = utcnow()
    ctx = {"kpis": {}}
    if current_user.can("application.view"):
        apps_q = access.application_scope(current_user, sa.select(Application))
        ctx["recent_apps"] = db.session.scalars(apps_q.order_by(Application.submitted_at.desc()).limit(8)).all()
        ctx["kpis"]["applications_30d"] = db.session.scalar(
            access.application_scope(current_user, sa.select(sa.func.count(Application.id)))
            .where(Application.submitted_at >= now - timedelta(days=30)))
        ctx["kpis"]["awaiting_review"] = db.session.scalar(
            access.application_scope(current_user, sa.select(sa.func.count(Application.id)))
            .where(Application.status.in_(("submitted", "under_review", "further_review"))))
    if current_user.can("job.view_internal"):
        ctx["kpis"]["open_vacancies"] = db.session.scalar(sa.select(sa.func.count(Job.id)).where(Job.status == "published"))
    upcoming_q = access.interview_scope(current_user, sa.select(Interview)).where(
        Interview.status == "scheduled", Interview.scheduled_at >= now - timedelta(hours=2),
        Interview.scheduled_at <= now + timedelta(days=14)).order_by(Interview.scheduled_at)
    ctx["upcoming"] = db.session.scalars(upcoming_q.limit(8)).all()
    ctx["kpis"]["interviews_14d"] = len(ctx["upcoming"])
    # Interviews where I still owe feedback
    submitted = sa.select(InterviewFeedback.interview_id).where(InterviewFeedback.interviewer_id == current_user.id)
    ctx["feedback_due"] = db.session.scalars(
        sa.select(Interview).join(InterviewerAssignment)
        .where(InterviewerAssignment.interviewer_id == current_user.id, Interview.status != "cancelled",
               Interview.scheduled_at <= now, Interview.id.not_in(submitted)).order_by(Interview.scheduled_at)).all()
    if current_user.can("decision.final"):
        ctx["awaiting_decision"] = db.session.scalars(
            access.application_scope(current_user, sa.select(Application)).where(Application.status == "interviewing")
            .limit(8)).all()
    if current_user.can("offer.approve"):
        ctx["kpis"]["offers_pending"] = db.session.scalar(
            sa.select(sa.func.count(Offer.id)).where(Offer.status == "pending_approval"))
    if current_user.can("employee.view"):
        emp_q = access.employee_scope(current_user, sa.select(sa.func.count(Employee.id)))
        ctx["kpis"]["onboarding"] = db.session.scalar(
            emp_q.where(Employee.employment_status.in_(("pre_boarding", "onboarding"))))
    if current_user.can("onboarding.manage"):
        ctx["kpis"]["overdue_tasks"] = db.session.scalar(sa.select(sa.func.count(OnboardingTask.id)).where(
            OnboardingTask.status != "completed", OnboardingTask.due_date < today_utc()))
    ctx["my_tasks"] = db.session.scalars(sa.select(OnboardingTask).where(
        OnboardingTask.assignee_user_id == current_user.id, OnboardingTask.status != "completed")
        .order_by(OnboardingTask.due_date).limit(6)).all()
    if current_user.can("clustering.view"):
        from app.services.job_clustering import is_stale, latest_run

        run = latest_run()
        ctx["cluster_run"] = run
        ctx["cluster_stale"] = is_stale(run) if run else True
    return render_template("dashboard/staff.html", **ctx)


@bp.get("/notifications")
@login_required
def notifications():
    page = paginate(sa.select(Notification).where(Notification.user_id == current_user.id)
                    .order_by(Notification.created_at.desc()))
    return render_template("notifications.html", page=page)


@bp.post("/notifications/read")
@login_required
def notifications_read():
    note_id = request.form.get("id", type=int)
    mark_read(current_user.id, note_id)
    db.session.commit()
    target = request.form.get("next")
    from app.security import is_safe_redirect

    return redirect(target if is_safe_redirect(target) else url_for("main.notifications"))
