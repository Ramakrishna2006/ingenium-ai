"""Recruiter-facing candidate directory, profiles and multi-job comparison."""
from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import current_user

from app import access
from app.errors import Forbidden, ValidationFailed
from app.extensions import db
from app.models import Application, Candidate, Job, Resume, User
from app.permissions import permission_required
from app.routes._helpers import arg_int, done, paginate
from app.services import recruitment_service as rs
from app.services.ai_service import AIError, get_ai
from app.services.matching import compare_to_jobs
from app.services.skills import normalize_skill_list

bp = Blueprint("candidates", __name__, url_prefix="/hr/candidates")


def _visible_candidate(candidate_id: int) -> Candidate:
    cand = access.get_or_404(Candidate, candidate_id, "Candidate not found.")
    if not access.can_view_candidate(current_user, cand) or not current_user.is_staff:
        raise Forbidden("You do not have access to this candidate.")
    return cand


@bp.get("/")
@permission_required("candidate.view")
def directory():
    q = (request.args.get("q") or "").strip()[:80]
    skill = (request.args.get("skill") or "").strip()[:60]
    job_id = arg_int("job")
    stmt = access.candidate_scope(current_user, sa.select(Candidate)).join(User, User.id == Candidate.user_id)
    if q:
        stmt = stmt.where(sa.or_(User.full_name.ilike(f"%{q}%"), Candidate.headline.ilike(f"%{q}%")))
    if job_id:
        stmt = stmt.where(Candidate.id.in_(sa.select(Application.candidate_id).where(Application.job_id == job_id)))
    candidates_all = None
    if skill:
        # JSON containment differs across databases; filter skill in Python on the scoped set.
        wanted = (normalize_skill_list([skill]) or [skill])[0].lower()
        candidates_all = [c for c in db.session.scalars(stmt.order_by(User.full_name)).all()
                          if wanted in {s.lower() for s in (c.skills or [])}]
    page = None if candidates_all is not None else paginate(stmt.order_by(User.full_name))
    jobs = db.session.scalars(sa.select(Job).where(Job.status.in_(("published", "paused", "closed"))).order_by(Job.title)).all()
    return render_template("candidates/directory.html", page=page, items=candidates_all, jobs=jobs,
                           filters={"q": q, "skill": skill, "job": job_id})


@bp.get("/<int:candidate_id>")
@permission_required("candidate.view")
def profile(candidate_id):
    cand = _visible_candidate(candidate_id)
    apps = db.session.scalars(access.application_scope(current_user, sa.select(Application))
                              .where(Application.candidate_id == cand.id).order_by(Application.submitted_at.desc())).all()
    limited = all(access.is_limited_viewer(current_user, a) for a in apps) if apps else not access.is_org_wide(current_user)
    open_jobs = db.session.scalars(sa.select(Job).where(Job.status == "published").order_by(Job.title)).all()
    return render_template("candidates/profile.html", cand=cand, apps=apps, limited=limited, open_jobs=open_jobs)


@bp.post("/<int:candidate_id>/compare")
@permission_required("application.screen")
def compare(candidate_id):
    cand = _visible_candidate(candidate_id)
    job_ids = [int(j) for j in request.form.getlist("job_ids") if j.isdigit()][:10]
    if not job_ids:
        job_ids = [a.job_id for a in cand.applications][:10]
    if not job_ids:
        raise ValidationFailed("Select at least one job to compare.")
    jobs = db.session.scalars(sa.select(Job).where(Job.id.in_(job_ids))).all()
    result = compare_to_jobs(cand, jobs)
    ai_text, ai_error = None, None
    if request.form.get("explain_with_ai"):
        try:
            ai = get_ai().explain_job_comparison([
                {k: r[k] for k in ("job_id", "job_title", "matched_skills", "claimed_skills", "missing_skills",
                                   "skill_overlap", "experience_check")} for r in result["rows"]])
            ai_text = ai.model_dump()
        except AIError as exc:
            ai_error = exc.message
    from app.audit import record_audit

    record_audit("candidate.job_comparison", "candidate", cand.id, {"jobs": job_ids})
    db.session.commit()
    return render_template("candidates/compare.html", cand=cand, result=result, ai=ai_text, ai_error=ai_error)


@bp.post("/resumes/<int:resume_id>/skills")
@permission_required("application.screen")
def correct_resume(resume_id):
    resume = access.get_or_404(Resume, resume_id)
    _visible_candidate(resume.candidate_id)
    rs.correct_resume_extraction(resume, normalize_skill_list(request.form.get("skills", "")), current_user)
    return done("Extracted skills corrected.", "candidates.profile", candidate_id=resume.candidate_id)
