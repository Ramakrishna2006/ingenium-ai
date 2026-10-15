"""Interview scheduling, interviewer feedback and AI interview support."""
from __future__ import annotations

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from flask import url_for

from app import access
from app.audit import record_audit
from app.errors import Conflict, Forbidden, InvalidTransition, ValidationFailed
from app.errors import ServiceUnavailable as ServiceUnavailableError
from app.extensions import db
from app.models import Interview, InterviewerAssignment, InterviewFeedback, User, utcnow
from app.models.base import as_utc
from app.permissions import STAFF_ROLES
from app.services.ai_service import AIError, get_ai
from app.services.notification_service import notify
from app.services.recruitment_service import transition_application


def to_utc(local_dt, tz_name: str):
    if local_dt.tzinfo is not None:
        return local_dt.astimezone(timezone.utc)
    return local_dt.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def local_time(interview: Interview, tz_name: str | None = None):
    return as_utc(interview.scheduled_at).astimezone(ZoneInfo(tz_name or interview.timezone))


def _interviewers(ids: list[int]) -> list[User]:
    users = list(db.session.scalars(sa.select(User).where(User.id.in_(set(ids)))))
    if len(users) != len(set(ids)):
        raise ValidationFailed("One or more interviewers were not found.")
    for u in users:
        if not u.active or u.role not in STAFF_ROLES or not u.can("interview.feedback"):
            raise ValidationFailed(f"{u.full_name} cannot be assigned as an interviewer.")
    return users


def _check_conflicts(users: list[User], start_utc, duration: int, exclude_id: int | None = None):
    end = start_utc + timedelta(minutes=duration)
    stmt = (sa.select(Interview).join(InterviewerAssignment)
            .where(InterviewerAssignment.interviewer_id.in_([u.id for u in users]), Interview.status == "scheduled"))
    if exclude_id:
        stmt = stmt.where(Interview.id != exclude_id)
    for other in db.session.scalars(stmt).unique():
        o_start = as_utc(other.scheduled_at)
        o_end = o_start + timedelta(minutes=other.duration_minutes)
        if o_start < end and start_utc < o_end:
            raise Conflict("An assigned interviewer already has an interview at that time.")


def schedule_interview(app, data, actor) -> Interview:
    if not access.can_schedule_interview(actor, app):
        raise Forbidden("You cannot schedule interviews for this application.")
    if app.status not in ("shortlisted", "interviewing"):
        raise InvalidTransition("Shortlist the application before scheduling interviews.")
    start = to_utc(data.scheduled_local, data.timezone)
    if start < utcnow() - timedelta(minutes=5):
        raise ValidationFailed("Interviews cannot be scheduled in the past.")
    users = _interviewers(data.interviewer_ids)
    _check_conflicts(users, start, data.duration_minutes)
    interview = Interview(
        application_id=app.id, kind=data.kind, scheduled_at=start, timezone=data.timezone,
        duration_minutes=data.duration_minutes, location=data.location, meeting_link=data.meeting_link,
        notes=data.notes, created_by_id=actor.id, status="scheduled",
    )
    db.session.add(interview)
    db.session.flush()
    for u in users:
        db.session.add(InterviewerAssignment(interview_id=interview.id, interviewer_id=u.id))
    if app.status == "shortlisted":
        transition_application(app, "interviewing", actor, "Interview scheduled", system=True)
    record_audit("interview.schedule", "interview", interview.id, {"application_id": app.id, "kind": data.kind})
    when = local_time(interview).strftime("%d %b %Y, %H:%M %Z")
    for u in users:
        notify(u, "Interview assigned", f"{app.job.title}: {interview.kind_label} interview on {when}",
               url_for("interviews.detail", interview_id=interview.id), "interview")
    notify(app.candidate.user_id, "Interview scheduled",
           f"{interview.kind_label} interview for {app.job.title} on {when}.",
           url_for("candidate.application_detail", app_id=app.id), "interview", email=True)
    db.session.flush()
    return interview


def reschedule_interview(interview: Interview, data, actor) -> Interview:
    if not access.can_schedule_interview(actor, interview.application):
        raise Forbidden()
    if interview.status != "scheduled":
        raise InvalidTransition("Only scheduled interviews can be rescheduled.")
    start = to_utc(data.scheduled_local, data.timezone)
    if start < utcnow():
        raise ValidationFailed("New time must be in the future.")
    users = [a.interviewer for a in interview.assignments]
    _check_conflicts(users, start, interview.duration_minutes, exclude_id=interview.id)
    old = as_utc(interview.scheduled_at).isoformat()
    interview.scheduled_at = start
    interview.timezone = data.timezone
    interview.reschedule_count += 1
    record_audit("interview.reschedule", "interview", interview.id, {"from": old, "to": start.isoformat(), "reason": data.reason})
    when = local_time(interview).strftime("%d %b %Y, %H:%M %Z")
    for u in users + [interview.application.candidate.user]:
        notify(u, "Interview rescheduled", f"{interview.application.job.title}: now {when}",
               url_for("interviews.detail", interview_id=interview.id) if u.is_staff
               else url_for("candidate.application_detail", app_id=interview.application_id), "interview")
    return interview


def set_interview_status(interview: Interview, status: str, reason: str | None, actor) -> Interview:
    app = interview.application
    allowed = access.can_schedule_interview(actor, app) or (
        status == "completed" and actor.id in interview.interviewer_ids)
    if not allowed:
        raise Forbidden()
    if interview.status != "scheduled":
        raise InvalidTransition(f"This interview is already {interview.status}.")
    if status == "completed" and as_utc(interview.scheduled_at) > utcnow():
        raise InvalidTransition("An interview cannot be completed before its start time.")
    if status == "cancelled" and not reason:
        raise ValidationFailed("Provide a cancellation reason.")
    interview.status = status
    if status == "completed":
        interview.completed_at = utcnow()
    else:
        interview.cancelled_reason = reason
        notify(app.candidate.user_id, "Interview cancelled", f"Your interview for {app.job.title} was cancelled.",
               url_for("candidate.application_detail", app_id=app.id), "interview")
    record_audit(f"interview.{status}", "interview", interview.id, {"reason": reason})
    return interview


def submit_feedback(interview: Interview, data, actor) -> InterviewFeedback:
    if actor.id not in interview.interviewer_ids or not actor.can("interview.feedback"):
        raise Forbidden("Only assigned interviewers can submit feedback.")
    if interview.status == "cancelled":
        raise InvalidTransition("Feedback cannot be submitted for a cancelled interview.")
    if as_utc(interview.scheduled_at) > utcnow():
        raise InvalidTransition("Feedback can be submitted once the interview has started.")
    existing = db.session.scalar(sa.select(InterviewFeedback).where(
        InterviewFeedback.interview_id == interview.id, InterviewFeedback.interviewer_id == actor.id))
    if existing:
        raise Conflict("You have already submitted feedback for this interview.")
    fb = InterviewFeedback(interview_id=interview.id, interviewer_id=actor.id, **data.model_dump())
    db.session.add(fb)
    if interview.status == "scheduled":
        interview.status = "completed"
        interview.completed_at = utcnow()
    db.session.flush()
    record_audit("interview.feedback", "interview", interview.id, {"recommendation": data.recommendation})
    job = interview.application.job
    for uid in {job.recruiter_id, job.hiring_manager_id} - {None, actor.id}:
        notify(uid, "Interview feedback submitted", f"{actor.full_name} – {job.title}",
               url_for("interviews.detail", interview_id=interview.id), "interview")
    return fb


def generate_questions(interview: Interview, actor) -> list[dict]:
    if not access.can_view_interview(actor, interview):
        raise Forbidden()
    job = interview.application.job
    screening = interview.application.latest_screening
    focus = list(job.required_skills or [])[:8]
    if screening:
        focus = list(dict.fromkeys((screening.effective_missing or []) + focus))[:10]
    try:
        qs = get_ai().interview_questions(
            {"title": job.title, "description": (job.description or "")[:1500], "skills": job.required_skills},
            interview.kind_label, focus)
    except AIError as exc:
        raise ServiceUnavailableError(exc.message) from exc
    interview.question_bank = [q.model_dump() for q in qs.questions]
    record_audit("interview.ai_questions", "interview", interview.id, {"count": len(qs.questions)})
    return interview.question_bank


def summarize_feedback(interview: Interview, actor) -> dict:
    if not access.can_view_all_feedback(actor, interview):
        raise Forbidden()
    if not interview.feedback:
        raise ValidationFailed("No feedback has been submitted yet.")
    items = [{"interviewer": f"Interviewer {i + 1}", "overall_rating": f.overall_rating,
              "recommendation": f.recommendation, "ratings": f.ratings, "strengths": f.strengths,
              "concerns": f.concerns} for i, f in enumerate(interview.feedback)]
    try:
        return get_ai().summarize_feedback(items).model_dump()
    except AIError as exc:
        raise ServiceUnavailableError(exc.message) from exc

