"""Candidate self-service portal. Every query is filtered by the signed-in candidate."""
from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app import access
from app.errors import Forbidden
from app.extensions import db
from app.models import Application, Offer, Resume
from app.routes._helpers import candidate_required, done, paginate
from app.schemas import CandidateProfileIn, OfferResponseIn, form_to_dict
from app.services import offer_service
from app.services import recruitment_service as rs
from app.services.matching import related_roles
from app.services.skills import normalize_skill_list

bp = Blueprint("candidate", __name__, url_prefix="/me")


def _own_application(app_id: int) -> Application:
    app = access.get_or_404(Application, app_id, "Application not found.")
    if not access.is_application_owner(current_user, app):
        raise Forbidden("This is not your application.")  # never reveal other candidates' data
    return app


@bp.get("/")
@candidate_required
def dashboard():
    cand = current_user.candidate
    apps = cand.applications.order_by(Application.submitted_at.desc()).all()
    offers = db.session.scalars(access.offer_scope(current_user).where(Offer.status == "approved")).all()
    fields = [cand.headline, cand.summary, cand.skills, cand.location, cand.phone, cand.resumes]
    completeness = round(100 * sum(1 for f in fields if f) / len(fields))
    suggestions = related_roles(cand, {a.job_id for a in apps}) if cand.resumes or cand.skills else []
    return render_template("candidate/dashboard.html", apps=apps, offers=offers, completeness=completeness,
                           suggestions=suggestions)


@bp.route("/profile", methods=["GET", "POST"])
@candidate_required
def profile():
    cand = current_user.candidate
    if request.method == "POST":
        data = CandidateProfileIn.model_validate(form_to_dict(request.form))
        rs.update_candidate_profile(cand, data, current_user)
        return done("Profile saved.", "candidate.profile")
    return render_template("candidate/profile.html", cand=cand)


@bp.get("/resumes")
@candidate_required
def resumes():
    return render_template("candidate/resumes.html", cand=current_user.candidate)


@bp.post("/resumes")
@candidate_required
def upload_resume():
    resume = rs.upload_resume(current_user.candidate, request.files.get("resume"), current_user)
    msg = {"parsed": "Resume uploaded and analysed. Review the extracted skills below.",
           "needs_ocr": "Resume uploaded, but it looks scanned — no text could be extracted. A text-based PDF or DOCX works best.",
           "failed": "Resume stored, but the text could not be read."}[resume.parse_status]
    return done(msg, "candidate.resumes", "success" if resume.parse_status == "parsed" else "warning")


def _own_resume(resume_id: int) -> Resume:
    resume = access.get_or_404(Resume, resume_id, "Resume not found.")
    if resume.candidate_id != current_user.candidate.id:
        raise Forbidden()
    return resume


@bp.post("/resumes/<int:resume_id>/primary")
@candidate_required
def primary_resume(resume_id):
    rs.set_primary_resume(_own_resume(resume_id), current_user)
    return done("Primary resume updated.", "candidate.resumes")


@bp.post("/resumes/<int:resume_id>/delete")
@candidate_required
def delete_resume(resume_id):
    rs.delete_resume(_own_resume(resume_id), current_user)
    return done("Resume deleted.", "candidate.resumes")


@bp.post("/resumes/<int:resume_id>/skills")
@candidate_required
def correct_skills(resume_id):
    resume = _own_resume(resume_id)
    skills = normalize_skill_list(request.form.get("skills", ""))
    rs.correct_resume_extraction(resume, skills, current_user)
    if request.form.get("apply_to_profile"):
        current_user.candidate.skills = normalize_skill_list(list(current_user.candidate.skills or []) + skills)
    return done("Extracted skills updated.", "candidate.resumes")


@bp.get("/applications")
@candidate_required
def applications():
    page = paginate(sa.select(Application).where(Application.candidate_id == current_user.candidate.id)
                    .order_by(Application.submitted_at.desc()))
    return render_template("candidate/applications.html", page=page)


@bp.get("/applications/<int:app_id>")
@candidate_required
def application_detail(app_id):
    app = _own_application(app_id)
    offer = next((o for o in app.offers if o.status in access.CANDIDATE_VISIBLE_OFFER_STATUSES), None)
    events = [e for e in app.events if not e.internal]
    return render_template("candidate/application_detail.html", app=app, offer=offer, events=events)


@bp.post("/applications/<int:app_id>/withdraw")
@candidate_required
def withdraw(app_id):
    app = _own_application(app_id)
    rs.withdraw_application(app, current_user, (request.form.get("reason") or "")[:500] or None)
    return done("Application withdrawn.", "candidate.applications", "info")


@bp.get("/offers/<int:offer_id>")
@candidate_required
def offer(offer_id):
    offer = access.get_or_404(Offer, offer_id, "Offer not found.")
    if not access.is_application_owner(current_user, offer.application) or not access.can_view_offer(current_user, offer):
        raise Forbidden("This offer is not available.")
    return render_template("candidate/offer.html", offer=offer)


@bp.post("/offers/<int:offer_id>/respond")
@candidate_required
def respond_offer(offer_id):
    data = OfferResponseIn.model_validate(form_to_dict(request.form))
    offer, employee = offer_service.respond_to_offer(offer_id, data.response, data.reason, current_user)
    db.session.commit()
    if employee is not None:
        flash(f"Congratulations! You've accepted the offer. Your employee ID is {employee.employee_code}.", "success")
        return redirect(url_for("portal.onboarding"))
    flash("Your response has been recorded.", "info")
    return redirect(url_for("candidate.offer", offer_id=offer.id))
