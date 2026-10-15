from __future__ import annotations

from datetime import timedelta
from itertools import groupby
from zoneinfo import available_timezones

import sqlalchemy as sa
from flask import Blueprint, flash, render_template, request
from flask_login import current_user

from app import access
from app.errors import Forbidden
from app.extensions import db
from app.models import Application, Interview, InterviewerAssignment, User, get_setting, utcnow
from app.models.base import as_utc
from app.models.hiring import INTERVIEW_KIND_LABELS, RATING_CRITERIA, RECOMMENDATION_LABELS
from app.permissions import STAFF_ROLES, permission_required
from app.routes._helpers import done
from app.schemas import FeedbackIn, InterviewIn, InterviewStatusIn, RescheduleIn, form_to_dict
from app.services import interview_service as ivs
from app.template_helpers import fmt_dt

bp = Blueprint("interviews", __name__, url_prefix="/hr/interviews")

COMMON_ZONES = ["Asia/Kolkata", "UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago",
                "America/Los_Angeles", "Asia/Singapore", "Asia/Dubai", "Australia/Sydney"]


def _zones():
    default = get_setting("default_timezone") or "UTC"
    rest = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/")))
    return [default] + [z for z in COMMON_ZONES if z != default], rest


@bp.get("/")
@permission_required("interview.view")
def calendar():
    view = request.args.get("view", "upcoming")
    now = utcnow()
    stmt = access.interview_scope(current_user, sa.select(Interview))
    if view == "past":
        stmt = stmt.where(Interview.scheduled_at < now).order_by(Interview.scheduled_at.desc())
    elif view == "mine":
        stmt = stmt.where(Interview.assignments.any(InterviewerAssignment.interviewer_id == current_user.id)
                          ).order_by(Interview.scheduled_at)
    else:
        stmt = stmt.where(Interview.scheduled_at >= now - timedelta(hours=3)).order_by(Interview.scheduled_at)
    interviews = db.session.scalars(stmt.limit(200)).all()
    days = [(day, list(items)) for day, items in groupby(interviews, key=lambda iv: fmt_dt(iv.scheduled_at, "%A, %d %B %Y"))]
    return render_template("interviews/calendar.html", days=days, view=view)


@bp.route("/new/<int:app_id>", methods=["GET", "POST"])
@permission_required("interview.schedule")
def new(app_id):
    app = access.get_or_404(Application, app_id)
    access.require(access.can_schedule_interview(current_user, app), "You cannot schedule interviews for this application.")
    if request.method == "POST":
        data = InterviewIn.model_validate(form_to_dict(request.form, list_fields=("interviewer_ids",)))
        interview = ivs.schedule_interview(app, data, current_user)
        return done("Interview scheduled and participants notified.", "interviews.detail", interview_id=interview.id)
    interviewers = [u for u in db.session.scalars(sa.select(User).where(User.role.in_(STAFF_ROLES), User.active.is_(True))
                                                  .order_by(User.full_name)) if u.can("interview.feedback")]
    common, others = _zones()
    return render_template("interviews/form.html", app=app, interviewers=interviewers, kinds=INTERVIEW_KIND_LABELS,
                           common_zones=common, other_zones=others)


def _visible(interview_id) -> Interview:
    iv = access.get_or_404(Interview, interview_id, "Interview not found.")
    if not access.can_view_interview(current_user, iv):
        raise Forbidden("You are not assigned to this interview.")
    return iv


@bp.get("/<int:interview_id>")
@permission_required("interview.view")
def detail(interview_id):
    iv = _visible(interview_id)
    app = iv.application
    mine = next((f for f in iv.feedback if f.interviewer_id == current_user.id), None)
    see_all = access.can_view_all_feedback(current_user, iv)
    common, others = _zones()
    return render_template(
        "interviews/detail.html", iv=iv, app=app, mine=mine, see_all=see_all,
        feedback=iv.feedback if see_all else ([mine] if mine else []),
        is_interviewer=current_user.id in iv.interviewer_ids,
        can_manage=access.can_schedule_interview(current_user, app), limited=access.is_limited_viewer(current_user, app),
        criteria=RATING_CRITERIA, recommendations=RECOMMENDATION_LABELS, common_zones=common, other_zones=others,
        local_start=ivs.local_time(iv), started=as_utc(iv.scheduled_at) <= utcnow(),
    )


@bp.post("/<int:interview_id>/feedback")
@permission_required("interview.feedback")
def feedback(interview_id):
    iv = _visible(interview_id)
    form = form_to_dict(request.form)
    form["ratings"] = {c: request.form.get(f"rating_{c}") for c in RATING_CRITERIA if request.form.get(f"rating_{c}")}
    data = FeedbackIn.model_validate(form)
    ivs.submit_feedback(iv, data, current_user)
    return done("Feedback submitted. Thank you!", "interviews.detail", interview_id=iv.id)


@bp.post("/<int:interview_id>/reschedule")
@permission_required("interview.schedule")
def reschedule(interview_id):
    iv = _visible(interview_id)
    ivs.reschedule_interview(iv, RescheduleIn.model_validate(form_to_dict(request.form)), current_user)
    return done("Interview rescheduled and participants notified.", "interviews.detail", interview_id=iv.id)


@bp.post("/<int:interview_id>/status")
@permission_required("interview.view")
def status(interview_id):
    iv = _visible(interview_id)
    data = InterviewStatusIn.model_validate(form_to_dict(request.form))
    ivs.set_interview_status(iv, data.status, data.reason, current_user)
    return done(f"Interview marked {data.status}.", "interviews.detail", interview_id=iv.id)


@bp.post("/<int:interview_id>/questions")
@permission_required("interview.view")
def questions(interview_id):
    iv = _visible(interview_id)
    qs = ivs.generate_questions(iv, current_user)
    return done(f"{len(qs)} suggested questions added. Edit or ignore them as you see fit.", "interviews.detail",
                interview_id=iv.id)


@bp.post("/<int:interview_id>/summary")
@permission_required("interview.view")
def summary(interview_id):
    iv = _visible(interview_id)
    result = ivs.summarize_feedback(iv, current_user)
    flash("AI summary (for reference only): " + result.get("summary", ""), "info")
    return done("Summary generated.", "interviews.detail", "secondary", interview_id=iv.id)
