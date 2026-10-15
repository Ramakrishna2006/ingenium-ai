from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, abort, render_template, request
from flask_login import current_user

from app.extensions import db
from app.models import Application, Department, Job
from app.models.recruitment import EMPLOYMENT_TYPE_LABELS
from app.routes._helpers import candidate_required, done, paginate
from app.schemas import ApplyIn, form_to_dict
from app.services import recruitment_service as rs

bp = Blueprint("careers", __name__, url_prefix="/careers")


@bp.get("/apply")
def apply_home():
    """Candidate entry page: how to apply, job search and the latest openings."""
    from app.models import get_setting

    jobs = db.session.scalars(sa.select(Job).where(Job.status == "published")
                              .order_by(Job.published_at.desc().nulls_last(), Job.id.desc()).limit(6)).all()
    open_count = db.session.scalar(sa.select(sa.func.count(Job.id)).where(Job.status == "published")) or 0
    departments = db.session.scalars(sa.select(Department).where(Department.is_active.is_(True)).order_by(Department.name)).all()
    return render_template("public/apply.html", jobs=jobs, open_count=open_count, departments=departments,
                           types=EMPLOYMENT_TYPE_LABELS, org_name=get_setting("org_name"))


@bp.get("/")
def list_jobs():
    q = (request.args.get("q") or "").strip()[:100]
    dept = request.args.get("department", type=int)
    etype = request.args.get("type")
    location = (request.args.get("location") or "").strip()[:80]
    stmt = sa.select(Job).where(Job.status == "published")
    if q:
        like = f"%{q}%"
        stmt = stmt.where(sa.or_(Job.title.ilike(like), Job.description.ilike(like)))
    if dept:
        stmt = stmt.where(Job.department_id == dept)
    if etype in EMPLOYMENT_TYPE_LABELS:
        stmt = stmt.where(Job.employment_type == etype)
    if location:
        cond = Job.location.ilike(f"%{location}%")
        if location.lower() == "remote":
            cond = sa.or_(cond, Job.is_remote.is_(True))
        stmt = stmt.where(cond)
    page = paginate(stmt.order_by(Job.published_at.desc().nulls_last(), Job.id.desc()), per_page=12)
    departments = db.session.scalars(sa.select(Department).where(Department.is_active.is_(True)).order_by(Department.name)).all()
    applied_ids = set()
    if current_user.is_authenticated and current_user.candidate:
        applied_ids = set(db.session.scalars(sa.select(Application.job_id).where(
            Application.candidate_id == current_user.candidate.id)))
    return render_template("public/jobs.html", page=page, departments=departments, types=EMPLOYMENT_TYPE_LABELS,
                           filters={"q": q, "department": dept, "type": etype, "location": location},
                           applied_ids=applied_ids)


@bp.get("/<int:job_id>")
def job_detail(job_id):
    job = db.session.get(Job, job_id)
    if job is None or job.status != "published":
        abort(404)
    application = None
    if current_user.is_authenticated and current_user.candidate:
        application = db.session.scalar(sa.select(Application).where(
            Application.candidate_id == current_user.candidate.id, Application.job_id == job.id))
    return render_template("public/job_detail.html", job=job, application=application)


@bp.post("/<int:job_id>/apply")
@candidate_required
def apply(job_id):
    data = ApplyIn.model_validate({**form_to_dict(request.form), "job_id": job_id})
    app = rs.apply_to_job(current_user.candidate, data, current_user)
    return done("Application submitted. You can track its progress here.", "candidate.application_detail", app_id=app.id)
