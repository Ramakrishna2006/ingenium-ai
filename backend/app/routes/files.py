"""Authorised downloads of private files. Nothing under UPLOAD_FOLDER is publicly served."""
from __future__ import annotations

from flask import Blueprint, send_file
from flask_login import current_user, login_required

from app import access
from app.audit import record_audit
from app.errors import Forbidden, NotFound
from app.extensions import db
from app.models import EmployeeDocument, Offer, Resume
from app.services import storage

bp = Blueprint("files", __name__, url_prefix="/files")


def _send(kind, stored, download_name, mimetype, inline=True):
    path = storage.path_for(kind, stored)
    return send_file(path, mimetype=mimetype, as_attachment=not inline, download_name=download_name, max_age=0)


@bp.get("/resumes/<int:resume_id>")
@login_required
def resume(resume_id):
    r = access.get_or_404(Resume, resume_id, "File not found.")
    # Staff who may browse this candidate, the candidate themself, or anyone (e.g. an assigned
    # interviewer) who may view an application this resume was submitted with.
    allowed = access.can_view_candidate(current_user, r.candidate) or any(
        a.resume_id == r.id and access.can_view_application(current_user, a) for a in r.candidate.applications)
    if not allowed:
        raise Forbidden("You cannot access this resume.")
    if current_user.candidate is None or current_user.candidate.id != r.candidate_id:
        record_audit("resume.view", "resume", r.id)
        db.session.commit()
    inline = r.content_type == "application/pdf"
    return _send("resumes", r.stored_filename, r.original_filename, r.content_type, inline)


@bp.get("/documents/<int:doc_id>")
@login_required
def document(doc_id):
    doc = access.get_or_404(EmployeeDocument, doc_id, "File not found.")
    if not doc.stored_filename:
        raise NotFound("No file uploaded yet.")
    allowed = doc.employee.user_id == current_user.id or current_user.can("onboarding.manage")
    if not allowed:
        raise Forbidden("You cannot access this document.")
    if doc.employee.user_id != current_user.id:
        record_audit("document.view", "employee_document", doc.id)
        db.session.commit()
    return _send("documents", doc.stored_filename, doc.original_filename or "document", doc.content_type,
                 inline=doc.content_type in ("application/pdf", "image/png", "image/jpeg"))


@bp.get("/offers/<int:offer_id>")
@login_required
def offer_letter(offer_id):
    offer = access.get_or_404(Offer, offer_id, "File not found.")
    if not access.can_view_offer(current_user, offer) or not offer.pdf_stored_filename:
        raise Forbidden("This offer letter is not available.")
    return _send("offers", offer.pdf_stored_filename, f"offer-letter-{offer.id}.pdf", "application/pdf")
