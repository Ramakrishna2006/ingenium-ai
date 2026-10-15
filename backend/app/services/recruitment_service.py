"""Business rules for departments, hiring requests, jobs, candidates, resumes,
applications, screening and hiring decisions.

Route handlers (HTML and API) call these functions; they validate permissions
and state transitions, write audit records and create notifications. Callers
commit the session (``db.session.commit()``) after a successful call.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from flask import current_app, url_for
from sqlalchemy.exc import IntegrityError

from app import access
from app.audit import record_audit
from app.errors import Conflict, Forbidden, InvalidTransition, NotFound, ValidationFailed
from app.extensions import db
from app.models import (
    Application, ApplicationEvent, Candidate, Department, HiringDecision, HiringRequest, Job, Resume,
    ScreeningResult, User, get_setting, utcnow,
)
from app.models.recruitment import APPLICATION_TERMINAL, APPLICATION_TRANSITIONS, HIRING_REQUEST_TRANSITIONS, JOB_TRANSITIONS
from app.permissions import HIRING_MANAGER, RECRUITER, STAFF_ROLES
from app.services import storage
from app.services.ai_service import AIError, get_ai
from app.services.matching import ANALYSIS_VERSION, compare_to_job
from app.services.notification_service import notify, notify_role
from app.services.resume_parser import parse_resume

log = logging.getLogger(__name__)
CANDIDATE_VISIBLE_STATUSES = {"submitted", "under_review", "shortlisted", "interviewing", "offered", "hired",
                              "rejected", "withdrawn", "offer_declined"}


# ------------------------------------------------------------------ helpers
def _user(user_id: int | None, roles=None, label="User") -> User | None:
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None or not user.active:
        raise ValidationFailed(f"{label} not found or inactive.")
    if roles and user.role not in roles:
        raise ValidationFailed(f"{label} must have one of the roles: {', '.join(sorted(roles))}.")
    return user


def _department(dept_id: int) -> Department:
    dept = db.session.get(Department, dept_id)
    if dept is None or not dept.is_active:
        raise ValidationFailed("Department not found or inactive.")
    return dept


# -------------------------------------------------------------- departments
def create_department(data, actor) -> Department:
    if not actor.can("department.manage"):
        raise Forbidden("Only HR managers and administrators can manage departments.")
    exists = db.session.scalar(sa.select(Department).where(
        sa.or_(sa.func.lower(Department.name) == data.name.lower(), Department.code == data.code)))
    if exists:
        raise Conflict("A department with this name or code already exists.")
    _user(data.head_user_id, STAFF_ROLES, "Department head")
    dept = Department(**data.model_dump())
    db.session.add(dept)
    db.session.flush()
    record_audit("department.create", "department", dept.id, {"name": dept.name, "code": dept.code})
    return dept


def update_department(dept: Department, data, actor) -> Department:
    if not actor.can("department.manage"):
        raise Forbidden("Only HR managers and administrators can manage departments.")
    clash = db.session.scalar(sa.select(Department).where(
        Department.id != dept.id,
        sa.or_(sa.func.lower(Department.name) == data.name.lower(), Department.code == data.code)))
    if clash:
        raise Conflict("Another department already uses this name or code.")
    _user(data.head_user_id, STAFF_ROLES, "Department head")
    for key, value in data.model_dump().items():
        setattr(dept, key, value)
    record_audit("department.update", "department", dept.id, {"name": dept.name, "active": dept.is_active})
    return dept


# ----------------------------------------------------------- hiring requests
def create_hiring_request(data, actor) -> HiringRequest:
    if not actor.can("hiring_request.create"):
        raise Forbidden()
    _department(data.department_id)
    _user(data.hiring_manager_id, {HIRING_MANAGER, "hr_manager", "super_admin"}, "Hiring manager")
    hr = HiringRequest(**data.model_dump(), requested_by_id=actor.id, status="pending")
    if hr.hiring_manager_id is None and actor.role == HIRING_MANAGER:
        hr.hiring_manager_id = actor.id
    db.session.add(hr)
    db.session.flush()
    record_audit("hiring_request.create", "hiring_request", hr.id, {"title": hr.title, "headcount": hr.headcount})
    notify_role(("hr_manager",), "New hiring request", f"{hr.title} ({hr.headcount})",
                url_for("jobs.hiring_requests"), "hiring_request")
    return hr


def update_hiring_request(hr: HiringRequest, data, actor) -> HiringRequest:
    if hr.status != "revision_requested":
        raise InvalidTransition("Only requests returned for revision can be edited.")
    if actor.id != hr.requested_by_id and not access.is_org_wide(actor):
        raise Forbidden()
    _department(data.department_id)
    for key, value in data.model_dump().items():
        setattr(hr, key, value)
    hr.status = "pending"
    record_audit("hiring_request.resubmit", "hiring_request", hr.id)
    return hr


def decide_hiring_request(hr: HiringRequest, decision: str, notes: str | None, actor) -> HiringRequest:
    if not actor.can("hiring_request.decide"):
        raise Forbidden("Only HR managers can decide hiring requests.")
    if decision not in HIRING_REQUEST_TRANSITIONS.get(hr.status, set()):
        raise InvalidTransition(f"Cannot change a {hr.status} request to {decision}.")
    if decision in ("rejected", "revision_requested") and not notes:
        raise ValidationFailed("Please add a note explaining the decision.")
    hr.status = decision
    hr.decision_notes = notes
    hr.decided_by_id = actor.id
    hr.decided_at = utcnow()
    record_audit(f"hiring_request.{decision}", "hiring_request", hr.id, {"notes": notes})
    notify(hr.requested_by_id, f"Hiring request {decision.replace('_', ' ')}", hr.title,
           url_for("jobs.hiring_requests"), "hiring_request")
    return hr


# ---------------------------------------------------------------------- jobs
def _job_people(data, actor):
    _user(data.recruiter_id, {RECRUITER, "hr_manager", "super_admin"}, "Recruiter")
    _user(data.hiring_manager_id, {HIRING_MANAGER, "hr_manager", "super_admin"}, "Hiring manager")


def create_job(data, actor) -> Job:
    if not actor.can("job.manage"):
        raise Forbidden("You cannot create vacancies.")
    _department(data.department_id)
    _job_people(data, actor)
    if data.hiring_request_id:
        hr = db.session.get(HiringRequest, data.hiring_request_id)
        if hr is None or hr.status != "approved":
            raise ValidationFailed("Vacancies can only be created from an approved hiring request.")
    job = Job(**data.model_dump(), status="draft", created_by_id=actor.id)
    if job.recruiter_id is None and actor.role == RECRUITER:
        job.recruiter_id = actor.id
    if job.hiring_request_id and job.hiring_manager_id is None:
        job.hiring_manager_id = db.session.get(HiringRequest, job.hiring_request_id).hiring_manager_id
    db.session.add(job)
    db.session.flush()
    record_audit("job.create", "job", job.id, {"title": job.title})
    return job


def update_job(job: Job, data, actor) -> Job:
    if not access.can_manage_job(actor, job):
        raise Forbidden("You cannot edit this vacancy.")
    if job.status == "archived":
        raise InvalidTransition("Archived vacancies cannot be edited.")
    _department(data.department_id)
    _job_people(data, actor)
    for key, value in data.model_dump().items():
        setattr(job, key, value)
    record_audit("job.update", "job", job.id, {"title": job.title})
    return job


def change_job_status(job: Job, new_status: str, actor) -> Job:
    if not access.can_manage_job(actor, job):
        raise Forbidden("You cannot change this vacancy.")
    if new_status not in JOB_TRANSITIONS.get(job.status, set()):
        raise InvalidTransition(f"A {job.status} vacancy cannot be moved to {new_status}.")
    if new_status == "published":
        if not (job.required_skills and len(job.description or "") >= 20):
            raise ValidationFailed("Add a description and at least one required skill before publishing.")
        job.published_at = job.published_at or utcnow()
        job.closed_at = None
    if new_status == "closed":
        job.closed_at = utcnow()
    old = job.status
    job.status = new_status
    record_audit("job.status", "job", job.id, {"from": old, "to": new_status})
    return job


# ---------------------------------------------------------------- candidates
def ensure_candidate_profile(user: User) -> Candidate:
    if user.candidate is None:
        cand = Candidate(user_id=user.id, skills=[], education=[], certifications=[])
        db.session.add(cand)
        db.session.flush()
        db.session.refresh(user)
    return user.candidate


def update_candidate_profile(candidate: Candidate, data, actor) -> Candidate:
    if not (actor.candidate and actor.candidate.id == candidate.id) and not (
        actor.can("application.screen") and access.can_view_candidate(actor, candidate)
    ):
        raise Forbidden("You cannot edit this profile.")
    values = data.model_dump()
    full_name = values.pop("full_name", None)
    if full_name and actor.candidate and actor.candidate.id == candidate.id:
        candidate.user.full_name = full_name
    for key, value in values.items():
        setattr(candidate, key, value)
    record_audit("candidate.update", "candidate", candidate.id, {"by_self": actor.candidate is not None and actor.candidate.id == candidate.id})
    return candidate


def upload_resume(candidate: Candidate, file_storage, actor) -> Resume:
    if not (actor.candidate and actor.candidate.id == candidate.id):
        raise Forbidden("Candidates upload their own resumes.")
    if len(candidate.resumes) >= 10:
        raise ValidationFailed("You can store at most 10 resumes. Delete an old one first.")
    stored, data = storage.save_upload("resumes", file_storage, storage.RESUME_EXTENSIONS)
    parsed = parse_resume(data, stored.extension, current_app.config.get("ENABLE_OCR", False))
    resume = Resume(
        candidate_id=candidate.id, original_filename=stored.original_filename,
        stored_filename=stored.stored_filename, content_type=stored.content_type,
        size_bytes=stored.size_bytes, sha256=stored.sha256, extracted_text=parsed.text or None,
        parse_status=parsed.status, parse_error=parsed.error, extracted=parsed.extracted,
        is_primary=not candidate.resumes,
    )
    db.session.add(resume)
    # Pre-fill empty profile fields from the resume (candidate can correct them).
    ext = parsed.extracted or {}
    if ext.get("skills") and not candidate.skills:
        candidate.skills = ext["skills"]
    if ext.get("education") and not candidate.education:
        candidate.education = ext["education"]
    if ext.get("certifications") and not candidate.certifications:
        candidate.certifications = ext["certifications"]
    if ext.get("years_experience") is not None and candidate.years_experience is None:
        candidate.years_experience = ext["years_experience"]
    db.session.flush()
    record_audit("resume.upload", "resume", resume.id, {"size": resume.size_bytes, "parse_status": resume.parse_status})
    return resume


def set_primary_resume(resume: Resume, actor) -> None:
    if not (actor.candidate and actor.candidate.id == resume.candidate_id):
        raise Forbidden()
    for r in resume.candidate.resumes:
        r.is_primary = r.id == resume.id


def delete_resume(resume: Resume, actor) -> None:
    if not (actor.candidate and actor.candidate.id == resume.candidate_id):
        raise Forbidden()
    in_use = db.session.scalar(sa.select(sa.func.count(Application.id)).where(
        Application.resume_id == resume.id, Application.status.not_in(APPLICATION_TERMINAL)))
    if in_use:
        raise Conflict("This resume is attached to an active application and cannot be deleted.")
    storage.delete_file("resumes", resume.stored_filename)
    record_audit("resume.delete", "resume", resume.id)
    was_primary = resume.is_primary
    candidate = resume.candidate
    db.session.delete(resume)
    db.session.flush()
    if was_primary and candidate.resumes:
        candidate.resumes[0].is_primary = True


def correct_resume_extraction(resume: Resume, skills: list[str], actor) -> Resume:
    if not (actor.candidate and actor.candidate.id == resume.candidate_id) and not access.can_view_candidate(actor, resume.candidate):
        raise Forbidden()
    extracted = dict(resume.extracted or {})
    extracted["skills"] = skills
    extracted["corrected_by"] = actor.id
    resume.extracted = extracted
    record_audit("resume.correct", "resume", resume.id, {"skills": len(skills)})
    return resume


# --------------------------------------------------------------- applications
def _event(app: Application, from_status, to_status, actor, reason=None, internal=False):
    db.session.add(ApplicationEvent(application_id=app.id, from_status=from_status, to_status=to_status,
                                    actor_id=getattr(actor, "id", None), reason=reason, internal=internal))


def apply_to_job(candidate: Candidate, data, actor) -> Application:
    if not (actor.candidate and actor.candidate.id == candidate.id and actor.can("self.candidate")):
        raise Forbidden("Only the candidate can submit their application.")
    job = db.session.get(Job, data.job_id)
    if job is None:
        raise NotFound("Job not found.")
    if not job.is_open_for_applications():
        raise Conflict("This vacancy is not accepting applications.")
    resume = None
    if data.resume_id is not None:
        resume = db.session.get(Resume, data.resume_id)
        if resume is None or resume.candidate_id != candidate.id:
            raise ValidationFailed("Select one of your own resumes.")
    else:
        resume = candidate.primary_resume
    if resume is None:
        raise ValidationFailed("Upload a resume before applying.")
    existing = db.session.scalar(sa.select(Application).where(
        Application.candidate_id == candidate.id, Application.job_id == job.id))
    if existing:
        raise Conflict("You have already applied for this vacancy.", code="duplicate_application")
    app = Application(candidate_id=candidate.id, job_id=job.id, resume_id=resume.id,
                      cover_letter=data.cover_letter, status="submitted")
    db.session.add(app)
    try:
        with db.session.begin_nested():
            db.session.flush()
    except IntegrityError as exc:  # concurrent duplicate submission
        raise Conflict("You have already applied for this vacancy.", code="duplicate_application") from exc
    _event(app, None, "submitted", actor, "Application submitted")
    record_audit("application.submit", "application", app.id, {"job_id": job.id})
    link = url_for("applications.detail", app_id=app.id)
    notify(job.recruiter_id, "New application", f"{candidate.user.full_name} applied for {job.title}", link, "application")
    notify(actor, "Application received", f"Your application for {job.title} was submitted.",
           url_for("candidate.application_detail", app_id=app.id), "application")
    return app


def transition_application(app: Application, to_status: str, actor, reason: str | None = None,
                           internal: bool = False, system: bool = False) -> Application:
    """Validated status change. ``system`` transitions come from workflow actions (interview, offer)."""
    if not system and not access.can_review_application(actor, app):
        raise Forbidden("You cannot change the status of this application.")
    if to_status not in APPLICATION_TRANSITIONS.get(app.status, set()):
        raise InvalidTransition(
            f"An application that is '{app.status_label}' cannot move to '{to_status.replace('_', ' ')}'.")
    old = app.status
    app.status = to_status
    app.status_changed_at = utcnow()
    _event(app, old, to_status, actor, reason, internal=internal)
    record_audit("application.status", "application", app.id, {"from": old, "to": to_status, "reason": reason})
    if to_status in CANDIDATE_VISIBLE_STATUSES and old != to_status:
        notify(app.candidate.user_id, "Application update",
               f"Your application for {app.job.title} is now: {app.status_label}.",
               url_for("candidate.application_detail", app_id=app.id), "application",
               email=to_status in ("shortlisted", "offered", "rejected"))
    return app


def review_application(app: Application, to_status: str, reason: str | None, actor) -> Application:
    if app.status == "submitted" and to_status != "under_review" and to_status != "rejected":
        transition_application(app, "under_review", actor, "Review started", internal=True)
    return transition_application(app, to_status, actor, reason, internal=to_status in ("on_hold", "further_review"))


def withdraw_application(app: Application, actor, reason: str | None = None) -> Application:
    if not access.is_application_owner(actor, app):
        raise Forbidden()
    if not get_setting("allow_application_withdrawal"):
        raise Conflict("Withdrawal is disabled by your organisation's policy. Please contact the recruiter.")
    if app.status in ("offered", "hired") or app.status in APPLICATION_TERMINAL:
        raise InvalidTransition("This application can no longer be withdrawn here.")
    return transition_application(app, "withdrawn", actor, reason or "Withdrawn by candidate", system=True)


# ------------------------------------------------------------------ screening
def run_screening(app: Application, actor, use_ai: bool = True) -> ScreeningResult:
    if not access.can_screen_application(actor, app):
        raise Forbidden("You cannot screen this application.")
    candidate = app.candidate
    resume = app.resume or candidate.primary_resume
    findings = compare_to_job(candidate, app.job, resume=resume)
    result = ScreeningResult(
        application_id=app.id, matched_skills=findings["matched_skills"] + findings["claimed_skills"],
        missing_skills=findings["missing_skills"], evidence=findings["evidence"],
        skill_overlap=findings["skill_overlap"], text_similarity=findings["text_similarity"],
        experience_summary=findings["explanation"], source="rules", analysis_version=ANALYSIS_VERSION,
        created_by_id=actor.id, verification_questions=[
            f"Can you describe a recent project where you used {s}?" for s in findings["claimed_skills"][:3]
        ] + [f"What exposure, if any, do you have to {s}?" for s in findings["missing_skills"][:3]],
    )
    if use_ai and current_app.config.get("AI_ENABLED") and resume is not None and resume.extracted_text:
        ai = get_ai()
        job_info = {"title": app.job.title, "required_skills": app.job.required_skills,
                    "preferred_skills": app.job.preferred_skills, "qualifications": app.job.qualifications,
                    "min_years": app.job.experience_min_years, "description": (app.job.description or "")[:1500]}
        rule_summary = {k: findings[k] for k in ("matched_skills", "claimed_skills", "missing_skills", "skill_overlap")}
        try:
            insight = ai.screening_insight(resume.extracted_text, job_info, rule_summary)
            result.ai_summary = insight.summary + ("\n\n" + insight.relevant_experience if insight.relevant_experience else "")
            if insight.verification_questions:
                result.verification_questions = insight.verification_questions
            ai_evidence = {e.skill.lower(): e.evidence for e in insight.matched_skills}
            for item in result.evidence:
                note = ai_evidence.get(item["skill"].lower())
                if note:
                    item["ai_note"] = note[:300]
            result.source = "rules+ai"
            result.ai_model = ai.model
        except AIError as exc:
            result.ai_error = exc.message[:255]
    elif use_ai and resume is not None and not resume.extracted_text:
        result.ai_error = "No resume text available for AI analysis."
    result.analysis_version = f"{ANALYSIS_VERSION}/{'ai-' + result.ai_model if result.ai_model else 'no-ai'}"[:20]
    db.session.add(result)
    db.session.flush()
    record_audit("screening.run", "application", app.id, {"source": result.source, "screening_id": result.id})
    return result


def override_screening(result: ScreeningResult, data, actor) -> ScreeningResult:
    if not access.can_screen_application(actor, result.application):
        raise Forbidden()
    result.corrected_matched_skills = data.matched_skills
    result.corrected_missing_skills = data.missing_skills
    result.reviewer_notes = data.reviewer_notes
    result.reviewed_by_id = actor.id
    result.reviewed_at = utcnow()
    record_audit("screening.override", "screening", result.id, {"matched": len(data.matched_skills)})
    return result


# ------------------------------------------------------------------ decisions
def record_decision(app: Application, decision: str, rationale: str, actor) -> HiringDecision:
    if decision in ("approve", "reject"):
        if not access.can_make_final_decision(actor, app):
            raise Forbidden("Only the job's hiring manager or HR can record a final decision.")
        if app.status != "interviewing":
            raise InvalidTransition("Final decisions are recorded after interviews have taken place.")
        completed = [iv for iv in app.interviews if iv.status == "completed"]
        if decision == "approve" and not completed:
            raise InvalidTransition("Complete at least one interview before approving for an offer.")
    else:
        if not access.can_recommend(actor, app):
            raise Forbidden("You cannot add a recommendation for this application.")
        if app.status in APPLICATION_TERMINAL:
            raise InvalidTransition("This application is closed.")
    hd = HiringDecision(application_id=app.id, decided_by_id=actor.id, decision=decision, rationale=rationale)
    db.session.add(hd)
    record_audit("hiring.decision", "application", app.id, {"decision": decision})
    if decision == "approve":
        transition_application(app, "approved_for_offer", actor, "Approved for offer", internal=True, system=True)
        notify_role(("hr_manager",), "Candidate approved for offer",
                    f"{app.candidate.user.full_name} – {app.job.title}", url_for("applications.detail", app_id=app.id), "offer")
    elif decision == "reject":
        transition_application(app, "rejected", actor, rationale, system=True)
    db.session.flush()
    return hd
