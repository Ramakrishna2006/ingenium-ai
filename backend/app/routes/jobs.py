"""Workforce planning, vacancy management and the job-clustering dashboard."""
from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import current_user

from app import access
from app.extensions import db
from app.models import Application, Department, HiringRequest, Job, User
from app.models.recruitment import EMPLOYMENT_TYPE_LABELS, JOB_STATUSES, JOB_TRANSITIONS
from app.permissions import HIRING_MANAGER, HR_MANAGER, RECRUITER, SUPER_ADMIN, permission_required
from app.routes._helpers import arg_int, done, paginate
from app.schemas import HiringRequestDecisionIn, HiringRequestIn, JobIn, JobStatusIn, form_to_dict
from app.services import job_clustering
from app.services import recruitment_service as rs

bp = Blueprint("jobs", __name__, url_prefix="/hr")


def _people(*roles):
    return db.session.scalars(sa.select(User).where(User.role.in_(roles), User.active.is_(True)).order_by(User.full_name)).all()


def _departments():
    return db.session.scalars(sa.select(Department).where(Department.is_active.is_(True)).order_by(Department.name)).all()


# ----------------------------------------------------------- hiring requests
@bp.get("/hiring-requests")
@permission_required("hiring_request.view")
def hiring_requests():
    status = request.args.get("status")
    stmt = sa.select(HiringRequest)
    if not access.is_org_wide(current_user):
        stmt = stmt.where(sa.or_(HiringRequest.requested_by_id == current_user.id,
                                 HiringRequest.hiring_manager_id == current_user.id,
                                 HiringRequest.status == "approved"))
    if status:
        stmt = stmt.where(HiringRequest.status == status)
    page = paginate(stmt.order_by(HiringRequest.created_at.desc()))
    return render_template("jobs/hiring_requests.html", page=page, status=status, departments=_departments(),
                           managers=_people(HIRING_MANAGER, HR_MANAGER, SUPER_ADMIN), types=EMPLOYMENT_TYPE_LABELS)


@bp.post("/hiring-requests")
@permission_required("hiring_request.create")
def create_hiring_request():
    data = HiringRequestIn.model_validate(form_to_dict(request.form))
    rs.create_hiring_request(data, current_user)
    return done("Hiring request submitted for approval.", "jobs.hiring_requests")


@bp.post("/hiring-requests/<int:hr_id>/decision")
@permission_required("hiring_request.decide")
def decide_hiring_request(hr_id):
    hr = access.get_or_404(HiringRequest, hr_id)
    data = HiringRequestDecisionIn.model_validate(form_to_dict(request.form))
    rs.decide_hiring_request(hr, data.decision, data.notes, current_user)
    return done(f"Hiring request {data.decision.replace('_', ' ')}.", "jobs.hiring_requests")


@bp.post("/hiring-requests/<int:hr_id>/resubmit")
@permission_required("hiring_request.create")
def resubmit_hiring_request(hr_id):
    hr = access.get_or_404(HiringRequest, hr_id)
    data = HiringRequestIn.model_validate(form_to_dict(request.form))
    rs.update_hiring_request(hr, data, current_user)
    return done("Hiring request resubmitted.", "jobs.hiring_requests")


# ------------------------------------------------------------------ vacancies
@bp.get("/jobs")
@permission_required("job.view_internal")
def manage_list():
    q = (request.args.get("q") or "").strip()[:100]
    status = request.args.get("status")
    dept = arg_int("department")
    mine = request.args.get("mine") == "1"
    stmt = sa.select(Job)
    if q:
        stmt = stmt.where(Job.title.ilike(f"%{q}%"))
    if status in JOB_STATUSES:
        stmt = stmt.where(Job.status == status)
    if dept:
        stmt = stmt.where(Job.department_id == dept)
    if mine:
        stmt = stmt.where(sa.or_(Job.recruiter_id == current_user.id, Job.hiring_manager_id == current_user.id,
                                 Job.created_by_id == current_user.id))
    page = paginate(stmt.order_by(Job.created_at.desc()))
    ids = [j.id for j in page.items]
    counts = dict(db.session.execute(sa.select(Application.job_id, sa.func.count(Application.id))
                                     .where(Application.job_id.in_(ids)).group_by(Application.job_id)).all())
    return render_template("jobs/manage_list.html", page=page, counts=counts, departments=_departments(), statuses=JOB_STATUSES,
                           filters={"q": q, "status": status, "department": dept, "mine": mine})


def _job_form_context(job=None):
    approved = db.session.scalars(sa.select(HiringRequest).where(HiringRequest.status == "approved")
                                  .order_by(HiringRequest.created_at.desc())).all()
    return {"job": job, "departments": _departments(), "types": EMPLOYMENT_TYPE_LABELS,
            "recruiters": _people(RECRUITER, HR_MANAGER, SUPER_ADMIN),
            "managers": _people(HIRING_MANAGER, HR_MANAGER, SUPER_ADMIN), "hiring_requests": approved}


@bp.route("/jobs/new", methods=["GET", "POST"])
@permission_required("job.manage")
def new_job():
    if request.method == "POST":
        data = JobIn.model_validate(form_to_dict(request.form, bool_fields=("is_remote",)))
        job = rs.create_job(data, current_user)
        if request.form.get("publish") == "1":
            rs.change_job_status(job, "published", current_user)
        return done("Vacancy saved.", "jobs.job_detail", job_id=job.id)
    ctx = _job_form_context()
    hr_id = arg_int("hiring_request")
    ctx["prefill"] = db.session.get(HiringRequest, hr_id) if hr_id else None
    return render_template("jobs/job_form.html", **ctx)


@bp.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@permission_required("job.manage")
def edit_job(job_id):
    job = access.get_or_404(Job, job_id)
    access.require(access.can_manage_job(current_user, job), "You cannot edit this vacancy.")
    if request.method == "POST":
        data = JobIn.model_validate(form_to_dict(request.form, bool_fields=("is_remote",)))
        rs.update_job(job, data, current_user)
        return done("Vacancy updated.", "jobs.job_detail", job_id=job.id)
    return render_template("jobs/job_form.html", **_job_form_context(job), prefill=None)


@bp.get("/jobs/<int:job_id>")
@permission_required("job.view_internal")
def job_detail(job_id):
    job = access.get_or_404(Job, job_id)
    apps_q = access.application_scope(current_user, sa.select(Application)).where(Application.job_id == job.id)
    apps = db.session.scalars(apps_q.order_by(Application.submitted_at.desc())).all()
    similar = job_clustering.similar_jobs(job) if current_user.can("clustering.view") else []
    cluster = job_clustering.cluster_of(job.id)
    return render_template("jobs/job_detail.html", job=job, apps=apps, similar=similar, cluster=cluster,
                           transitions=sorted(JOB_TRANSITIONS.get(job.status, set())),
                           can_manage=access.can_manage_job(current_user, job))


@bp.post("/jobs/<int:job_id>/status")
@permission_required("job.manage")
def job_status(job_id):
    job = access.get_or_404(Job, job_id)
    data = JobStatusIn.model_validate(form_to_dict(request.form))
    rs.change_job_status(job, data.status, current_user)
    return done(f"Vacancy is now {data.status}.", "jobs.job_detail", job_id=job.id)


# ------------------------------------------------------------------ clusters
@bp.get("/jobs/clusters")
@permission_required("clustering.view")
def clusters():
    run = job_clustering.latest_run()
    q = (request.args.get("q") or "").strip().lower()[:80]
    selected = request.args.get("cluster", type=int)
    jobs_by_id = {}
    if run:
        ids = [jid for c in run.clusters or [] for jid in c.get("job_ids", [])]
        jobs_by_id = {j.id: j for j in db.session.scalars(sa.select(Job).where(Job.id.in_(ids)))}
    groups = []
    for c in (run.clusters if run else []) or []:
        if selected is not None and c["index"] != selected:
            continue
        jobs = [jobs_by_id[j] for j in c.get("job_ids", []) if j in jobs_by_id]
        if q:
            jobs = [j for j in jobs if q in j.title.lower() or q in " ".join(j.required_skills or []).lower()
                    or any(q in k for k in c.get("keywords", []))]
        groups.append({**c, "jobs": jobs})
    if q:
        groups = [g for g in groups if g["jobs"]]
    sims = {a.job_id: a.centroid_similarity for a in (run.assignments if run else [])}
    return render_template("jobs/clusters.html", run=run, groups=groups, sims=sims,
                           stale=job_clustering.is_stale(run), q=q, selected=selected,
                           published_count=len(job_clustering.published_jobs()))


@bp.post("/jobs/clusters/recompute")
@permission_required("clustering.run")
def recompute_clusters():
    run = job_clustering.recompute_clusters(current_user.id)
    from app.audit import record_audit

    record_audit("clustering.recompute", "job_cluster_run", run.id, {"status": run.status, "k": run.n_clusters})
    return done(f"Clustering finished: {run.message}", "jobs.clusters",
                "success" if run.status == "completed" else "warning")
