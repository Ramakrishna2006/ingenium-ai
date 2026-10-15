"""Recruitment pipeline, application review, AI-assisted screening and hiring decisions."""
from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import current_user

from app import access
from app.errors import Forbidden
from app.extensions import db
from app.models import Application, Job, ScreeningResult
from app.models.recruitment import APPLICATION_STATUS_LABELS, APPLICATION_TRANSITIONS, MANUAL_REVIEW_STATUSES
from app.permissions import permission_required
from app.routes._helpers import arg_int, done
from app.schemas import ApplicationStatusIn, DecisionIn, ScreeningOverrideIn, form_to_dict
from app.services import recruitment_service as rs

bp = Blueprint("applications", __name__, url_prefix="/hr/applications")

PIPELINE_COLUMNS = [
    ("New", ("submitted",)),
    ("In review", ("under_review", "further_review", "on_hold")),
    ("Shortlisted", ("shortlisted",)),
    ("Interviewing", ("interviewing",)),
    ("Offer stage", ("approved_for_offer", "offered")),
    ("Hired", ("hired",)),
    ("Closed", ("rejected", "withdrawn", "offer_declined")),
]


def _visible(app_id: int) -> Application:
    app = access.get_or_404(Application, app_id, "Application not found.")
    if not current_user.is_staff or not access.can_view_application(current_user, app):
        raise Forbidden("You do not have access to this application.")
    return app


@bp.get("/")
@permission_required("application.view")
def pipeline():
    job_id = arg_int("job")
    q = (request.args.get("q") or "").strip()[:80]
    stmt = access.application_scope(current_user, sa.select(Application))
    if job_id:
        stmt = stmt.where(Application.job_id == job_id)
    if q:
        from app.models import Candidate, User

        stmt = stmt.join(Candidate).join(User, User.id == Candidate.user_id).where(User.full_name.ilike(f"%{q}%"))
    apps = db.session.scalars(stmt.order_by(Application.status_changed_at.desc()).limit(500)).all()
    columns = [(label, [a for a in apps if a.status in statuses]) for label, statuses in PIPELINE_COLUMNS]
    visible_jobs = sorted({a.job for a in db.session.scalars(access.application_scope(current_user, sa.select(Application))).all()},
                          key=lambda j: j.title)
    return render_template("applications/pipeline.html", columns=columns, jobs=visible_jobs, job_id=job_id, q=q,
                           total=len(apps))


@bp.get("/<int:app_id>")
@permission_required("application.view")
def detail(app_id):
    app = _visible(app_id)
    screening = app.latest_screening
    limited = access.is_limited_viewer(current_user, app)
    manual_options = [s for s in MANUAL_REVIEW_STATUSES
                      if s in APPLICATION_TRANSITIONS.get(app.status, set())
                      or (app.status == "submitted" and s in APPLICATION_TRANSITIONS["under_review"])]
    return render_template(
        "applications/detail.html", app=app, screening=screening, limited=limited,
        can_review=access.can_review_application(current_user, app),
        can_screen=access.can_screen_application(current_user, app),
        can_schedule=access.can_schedule_interview(current_user, app) and app.status in ("shortlisted", "interviewing"),
        can_final=access.can_make_final_decision(current_user, app),
        can_recommend=access.can_recommend(current_user, app),
        can_offer=current_user.can("offer.create") and app.status == "approved_for_offer",
        manual_options=manual_options, labels=APPLICATION_STATUS_LABELS,
    )


@bp.post("/<int:app_id>/status")
@permission_required("application.review")
def change_status(app_id):
    app = _visible(app_id)
    data = ApplicationStatusIn.model_validate(form_to_dict(request.form))
    rs.review_application(app, data.status, data.reason, current_user)
    return done(f"Application moved to {APPLICATION_STATUS_LABELS[data.status]}.", "applications.detail", app_id=app.id)


@bp.post("/<int:app_id>/screen")
@permission_required("application.screen")
def screen(app_id):
    app = _visible(app_id)
    result = rs.run_screening(app, current_user, use_ai=request.form.get("use_ai") == "1")
    if result.ai_error:
        return done(f"Rule-based screening saved. AI summary unavailable: {result.ai_error}", "applications.detail",
                    "warning", app_id=app.id)
    return done("Screening evidence generated. Review it before making any decision.", "applications.detail", app_id=app.id)


@bp.post("/screenings/<int:screening_id>/override")
@permission_required("application.screen")
def override(screening_id):
    result = access.get_or_404(ScreeningResult, screening_id)
    _visible(result.application_id)
    data = ScreeningOverrideIn.model_validate(form_to_dict(request.form))
    rs.override_screening(result, data, current_user)
    return done("Reviewer corrections saved.", "applications.detail", app_id=result.application_id)


@bp.post("/<int:app_id>/decision")
@permission_required("application.view")
def decision(app_id):
    app = _visible(app_id)
    data = DecisionIn.model_validate(form_to_dict(request.form))
    rs.record_decision(app, data.decision, data.rationale, current_user)
    return done("Decision recorded.", "applications.detail", app_id=app.id)


@bp.get("/by-job/<int:job_id>")
@permission_required("application.view")
def by_job(job_id):
    access.get_or_404(Job, job_id)
    from flask import redirect, url_for

    return redirect(url_for("applications.pipeline", job=job_id))
