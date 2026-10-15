"""REST API v1 (JSON). Session authentication + CSRF header (X-CSRFToken) for unsafe methods.

Every endpoint reuses the same service layer and access rules as the HTML pages.
See docs/API.md for the full reference.
"""
from __future__ import annotations

from functools import wraps

import sqlalchemy as sa
from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_user, logout_user
from flask_wtf.csrf import generate_csrf

from app import access
from app import serializers as S
from app.audit import record_audit
from app.errors import Forbidden, NotFound, ServiceUnavailable, ValidationFailed
from app.extensions import db
from app.models import (
    Application, Department, EmployeeDocument, HiringRequest, Interview, Job, Notification, Offer,
    OnboardingTask, ScreeningResult, TrainingAssignment, utcnow,
)
from app.permissions import ALL_PERMISSIONS, permission_required
from app.schemas import (
    ApplicationStatusIn, ApplyIn, CandidateProfileIn, CompareJobsIn, DecisionIn, DepartmentIn, DocumentReviewIn,
    FeedbackIn, HiringRequestDecisionIn, HiringRequestIn, InterviewIn, InterviewStatusIn, JobDraftIn, JobIn,
    JobStatusIn, LoginIn, OfferDecisionIn, OfferIn, OfferResponseIn, RegisterIn, RescheduleIn, ScreeningOverrideIn,
    TaskIn, TaskStatusIn, TrainingStatusIn,
)
from app.security import rate_limit
from app.services import interview_service as ivs
from app.services import job_clustering, offer_service
from app.services import onboarding_service as obs
from app.services import recruitment_service as rs
from app.services.ai_service import AIError, get_ai
from app.services.analytics_service import compute_metrics
from app.services.matching import compare_to_jobs
from app.services.notification_service import mark_read

bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ------------------------------------------------------------------ helpers
def login_required_json(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            from flask import abort

            abort(401)
        return view(*args, **kwargs)

    return wrapped


def body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        if request.mimetype == "application/json" and request.content_length:
            raise ValidationFailed("Malformed JSON body.")
        return {}
    if not isinstance(data, dict):
        raise ValidationFailed("JSON body must be an object.")
    return data


def paged(stmt, serializer):
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = min(max(request.args.get("per_page", 20, type=int), 1), 100)
    result = db.paginate(stmt, page=page, per_page=per_page, error_out=False)
    return jsonify(items=[serializer(i) for i in result.items], page=page, per_page=per_page,
                   total=result.total, pages=result.pages)


def created(payload, status=201):
    db.session.commit()
    return jsonify(payload), status


def ok(payload):
    db.session.commit()
    return jsonify(payload)


# ------------------------------------------------------------- health / auth
@bp.get("/health")
def health():
    db_ok = True
    try:
        db.session.execute(sa.text("SELECT 1"))
    except Exception:
        db_ok = False
    return jsonify(status="ok" if db_ok else "degraded", database=db_ok), (200 if db_ok else 503)


@bp.get("/auth/csrf")
def csrf_token():
    return jsonify(csrf_token=generate_csrf())


@bp.post("/auth/login")
@rate_limit(config_key="LOGIN_RATE_LIMIT")
def api_login():
    from app.routes.auth import authenticate

    data = LoginIn.model_validate(body())
    user = authenticate(data.email, data.password)
    if user is None:
        record_audit("auth.login_failed", "user", None, {"channel": "api"}, actor=None)
        db.session.commit()
        from app.errors import AppError

        raise AppError("Invalid email or password.", code="invalid_credentials", status_code=401)
    session.clear()
    session.permanent = True
    login_user(user, remember=data.remember)
    user.last_login_at = utcnow()
    record_audit("auth.login", "user", user.id, {"channel": "api"}, actor=user)
    return ok({"user": S.user_json(user), "csrf_token": generate_csrf()})


@bp.post("/auth/register")
@rate_limit("5/600")
def api_register():
    from app.routes.auth import register_candidate

    user = register_candidate(RegisterIn.model_validate(body()))
    return created({"user": S.user_json(user)})


@bp.post("/auth/logout")
@login_required_json
def api_logout():
    logout_user()
    session.clear()
    return jsonify(status="signed_out")


@bp.get("/auth/me")
@login_required_json
def me():
    perms = sorted(p for p in ALL_PERMISSIONS if current_user.can(p))
    return jsonify(user=S.user_json(current_user), permissions=perms)


# --------------------------------------------------------------- departments
@bp.get("/departments")
@login_required_json
@permission_required("department.view")
def list_departments():
    return paged(sa.select(Department).order_by(Department.name), S.department_json)


@bp.post("/departments")
@permission_required("department.manage")
def create_department():
    dept = rs.create_department(DepartmentIn.model_validate(body()), current_user)
    return created(S.department_json(dept))


@bp.patch("/departments/<int:dept_id>")
@permission_required("department.manage")
def update_department(dept_id):
    dept = access.get_or_404(Department, dept_id)
    merged = {**S.department_json(dept), **body()}
    rs.update_department(dept, DepartmentIn.model_validate(merged), current_user)
    return ok(S.department_json(dept))


# ----------------------------------------------------------- hiring requests
@bp.get("/hiring-requests")
@permission_required("hiring_request.view")
def list_hiring_requests():
    stmt = sa.select(HiringRequest)
    if not access.is_org_wide(current_user):
        stmt = stmt.where(sa.or_(HiringRequest.requested_by_id == current_user.id,
                                 HiringRequest.hiring_manager_id == current_user.id))
    if status := request.args.get("status"):
        stmt = stmt.where(HiringRequest.status == status)
    return paged(stmt.order_by(HiringRequest.created_at.desc()), S.hiring_request_json)


@bp.post("/hiring-requests")
@permission_required("hiring_request.create")
def create_hiring_request():
    hr = rs.create_hiring_request(HiringRequestIn.model_validate(body()), current_user)
    return created(S.hiring_request_json(hr))


@bp.post("/hiring-requests/<int:hr_id>/decision")
@permission_required("hiring_request.decide")
def decide_hiring_request(hr_id):
    hr = access.get_or_404(HiringRequest, hr_id)
    data = HiringRequestDecisionIn.model_validate(body())
    rs.decide_hiring_request(hr, data.decision, data.notes, current_user)
    return ok(S.hiring_request_json(hr))


# ---------------------------------------------------------------------- jobs
@bp.get("/jobs")
def list_jobs():
    internal = current_user.is_authenticated and current_user.can("job.view_internal") and request.args.get("scope") == "all"
    stmt = sa.select(Job)
    if not internal:
        stmt = stmt.where(Job.status == "published")
    elif status := request.args.get("status"):
        stmt = stmt.where(Job.status == status)
    if q := (request.args.get("q") or "").strip()[:100]:
        stmt = stmt.where(sa.or_(Job.title.ilike(f"%{q}%"), Job.description.ilike(f"%{q}%")))
    if dept := request.args.get("department_id", type=int):
        stmt = stmt.where(Job.department_id == dept)
    if etype := request.args.get("employment_type"):
        stmt = stmt.where(Job.employment_type == etype)
    return paged(stmt.order_by(Job.id.desc()), lambda j: S.job_json(j, internal))


@bp.post("/jobs")
@permission_required("job.manage")
def create_job():
    job = rs.create_job(JobIn.model_validate(body()), current_user)
    return created(S.job_json(job, True))


def _job_visible(job_id) -> tuple[Job, bool]:
    job = access.get_or_404(Job, job_id, "Job not found.")
    internal = current_user.is_authenticated and current_user.can("job.view_internal")
    if job.status != "published" and not internal:
        raise NotFound("Job not found.")
    return job, internal


@bp.get("/jobs/<int:job_id>")
def get_job(job_id):
    job, internal = _job_visible(job_id)
    return jsonify(S.job_json(job, internal))


@bp.patch("/jobs/<int:job_id>")
@permission_required("job.manage")
def update_job(job_id):
    job = access.get_or_404(Job, job_id)
    merged = {**S.job_json(job, True), "department_id": job.department_id, **body()}
    rs.update_job(job, JobIn.model_validate(merged), current_user)
    return ok(S.job_json(job, True))


@bp.post("/jobs/<int:job_id>/status")
@permission_required("job.manage")
def job_status(job_id):
    job = access.get_or_404(Job, job_id)
    rs.change_job_status(job, JobStatusIn.model_validate(body()).status, current_user)
    return ok(S.job_json(job, True))


@bp.post("/jobs/draft-description")
@permission_required("job.manage")
@rate_limit("20/60")
def draft_description():
    data = JobDraftIn.model_validate(body())
    try:
        draft = get_ai().draft_job_description(data.title, data.department, data.skills, data.notes)
    except AIError as exc:
        raise ServiceUnavailable(exc.message, code=f"ai_{exc.code}") from exc
    return jsonify(draft.model_dump(), ai_generated=True)


# ----------------------------------------------------------------- clustering
@bp.get("/jobs/clusters")
@permission_required("clustering.view")
def get_clusters():
    run = job_clustering.latest_run()
    if run is None:
        return jsonify(run=None, stale=True, message="No clustering run yet. POST /api/v1/jobs/clusters/recompute.")
    return jsonify(run=S.cluster_run_json(run, job_clustering.is_stale(run)))


@bp.post("/jobs/clusters/recompute")
@permission_required("clustering.run")
@rate_limit("10/60")
def recompute_clusters():
    max_k = body().get("max_k", job_clustering.MAX_K)
    if not isinstance(max_k, int) or not 2 <= max_k <= 15:
        raise ValidationFailed("max_k must be an integer between 2 and 15.")
    run = job_clustering.recompute_clusters(current_user.id, max_k=max_k)
    record_audit("clustering.recompute", "job_cluster_run", run.id, {"status": run.status})
    return created(S.cluster_run_json(run, False))


@bp.get("/jobs/clusters/<int:cluster_id>/jobs")
@permission_required("clustering.view")
def cluster_jobs(cluster_id):
    run = job_clustering.latest_run()
    cluster = next((c for c in (run.clusters if run else []) or [] if c["index"] == cluster_id), None)
    if cluster is None:
        raise NotFound("Cluster not found in the latest run.")
    jobs = db.session.scalars(sa.select(Job).where(Job.id.in_(cluster["job_ids"]))).all()
    sims = {a.job_id: a.centroid_similarity for a in run.assignments}
    return jsonify(run_id=run.id, cluster={k: cluster[k] for k in ("index", "label", "keywords", "size")},
                   jobs=[{**S.job_json(j, True), "centroid_similarity": sims.get(j.id)} for j in jobs])


@bp.get("/jobs/<int:job_id>/similar")
@permission_required("clustering.view")
def similar(job_id):
    job = access.get_or_404(Job, job_id)
    top = min(max(request.args.get("limit", 5, type=int), 1), 20)
    results = job_clustering.similar_jobs(job, top_n=top)
    return jsonify(job_id=job.id, method="tfidf-cosine", results=[
        {"job": S.job_json(r["job"]), "similarity": r["similarity"], "shared_skills": r["shared_skills"],
         "same_cluster": r["same_cluster"]} for r in results])


# ---------------------------------------------------------------- candidates
@bp.get("/candidates")
@permission_required("candidate.view")
def list_candidates():
    stmt = access.candidate_scope(current_user).order_by(sa.text("candidates.id desc"))
    return paged(stmt, lambda c: S.candidate_json(c, limited=not access.is_org_wide(current_user)))


@bp.get("/candidates/me")
@permission_required("self.candidate")
def my_candidate():
    return jsonify(S.candidate_json(rs.ensure_candidate_profile(current_user)))


@bp.patch("/candidates/me")
@permission_required("self.candidate")
def update_my_candidate():
    cand = rs.ensure_candidate_profile(current_user)
    rs.update_candidate_profile(cand, CandidateProfileIn.model_validate(body()), current_user)
    return ok(S.candidate_json(cand))


@bp.post("/candidates/me/resumes")
@permission_required("self.candidate")
@rate_limit("20/600")
def upload_resume():
    cand = rs.ensure_candidate_profile(current_user)
    resume = rs.upload_resume(cand, request.files.get("resume"), current_user)
    return created(S.resume_json(resume))


@bp.get("/candidates/<int:candidate_id>")
@login_required_json
def get_candidate(candidate_id):
    from app.models import Candidate

    cand = access.get_or_404(Candidate, candidate_id, "Candidate not found.")
    if not access.can_view_candidate(current_user, cand):
        raise Forbidden("You do not have access to this candidate.")
    own = current_user.candidate is not None and current_user.candidate.id == cand.id
    return jsonify(S.candidate_json(cand, limited=not (own or access.is_org_wide(current_user)
                                                        or current_user.can("application.screen"))))


@bp.post("/candidates/<int:candidate_id>/job-comparison")
@permission_required("application.screen")
def job_comparison(candidate_id):
    from app.models import Candidate

    cand = access.get_or_404(Candidate, candidate_id, "Candidate not found.")
    if not access.can_view_candidate(current_user, cand):
        raise Forbidden("You do not have access to this candidate.")
    data = CompareJobsIn.model_validate(body())
    ids = data.job_ids or [a.job_id for a in cand.applications][:10]
    if not ids:
        raise ValidationFailed("Provide job_ids or compare a candidate who has applications.")
    jobs = db.session.scalars(sa.select(Job).where(Job.id.in_(ids))).all()
    if len(jobs) != len(set(ids)):
        raise NotFound("One or more jobs were not found.")
    result = compare_to_jobs(cand, jobs)
    if data.explain_with_ai:
        try:
            result["ai_explanation"] = get_ai().explain_job_comparison(
                [{k: r[k] for k in ("job_id", "job_title", "matched_skills", "claimed_skills", "missing_skills",
                                    "skill_overlap", "experience_check")} for r in result["rows"]]).model_dump()
        except AIError as exc:
            result["ai_error"] = exc.message
    record_audit("candidate.job_comparison", "candidate", cand.id, {"jobs": ids, "channel": "api"})
    db.session.commit()
    return jsonify(result)


# -------------------------------------------------------------- applications
def _app_visible(app_id) -> Application:
    app = access.get_or_404(Application, app_id, "Application not found.")
    if not access.can_view_application(current_user, app):
        raise NotFound("Application not found.")  # do not reveal existence
    return app


@bp.get("/applications")
@login_required_json
def list_applications():
    stmt = access.application_scope(current_user)
    if status := request.args.get("status"):
        stmt = stmt.where(Application.status == status)
    if job_id := request.args.get("job_id", type=int):
        stmt = stmt.where(Application.job_id == job_id)
    staff = current_user.is_staff
    return paged(stmt.order_by(Application.submitted_at.desc()), lambda a: S.application_json(a, staff))


@bp.post("/applications")
@permission_required("self.candidate")
@rate_limit("30/600")
def create_application():
    cand = rs.ensure_candidate_profile(current_user)
    app = rs.apply_to_job(cand, ApplyIn.model_validate(body()), current_user)
    return created(S.application_json(app, staff=False))


@bp.get("/applications/<int:app_id>")
@login_required_json
def get_application(app_id):
    app = _app_visible(app_id)
    return jsonify(S.application_json(app, staff=current_user.is_staff))


@bp.post("/applications/<int:app_id>/status")
@permission_required("application.review")
def application_status(app_id):
    app = _app_visible(app_id)
    data = ApplicationStatusIn.model_validate(body())
    rs.review_application(app, data.status, data.reason, current_user)
    return ok(S.application_json(app))


@bp.post("/applications/<int:app_id>/withdraw")
@permission_required("self.candidate")
def withdraw(app_id):
    app = _app_visible(app_id)
    rs.withdraw_application(app, current_user, body().get("reason"))
    return ok(S.application_json(app, staff=False))


@bp.post("/applications/<int:app_id>/screening")
@permission_required("application.screen")
def run_screening(app_id):
    app = _app_visible(app_id)
    result = rs.run_screening(app, current_user, use_ai=bool(body().get("use_ai", True)))
    return created(S.screening_json(result))


@bp.get("/applications/<int:app_id>/screening")
@permission_required("application.screen")
def get_screening(app_id):
    app = _app_visible(app_id)
    access.require(access.can_screen_application(current_user, app))
    return jsonify(items=[S.screening_json(s) for s in app.screenings])


@bp.patch("/screenings/<int:screening_id>")
@permission_required("application.screen")
def override_screening(screening_id):
    result = access.get_or_404(ScreeningResult, screening_id)
    _app_visible(result.application_id)
    rs.override_screening(result, ScreeningOverrideIn.model_validate(body()), current_user)
    return ok(S.screening_json(result))


@bp.get("/applications/<int:app_id>/decisions")
@permission_required("application.view")
def list_decisions(app_id):
    app = _app_visible(app_id)
    access.require(current_user.is_staff)
    return jsonify(items=[S.decision_json(d) for d in app.decisions])


@bp.post("/applications/<int:app_id>/decisions")
@permission_required("application.view")
def create_decision(app_id):
    app = _app_visible(app_id)
    data = DecisionIn.model_validate(body())
    decision = rs.record_decision(app, data.decision, data.rationale, current_user)
    return created(S.decision_json(decision))


# ------------------------------------------------------------------ interviews
@bp.get("/interviews")
@permission_required("interview.view")
def list_interviews():
    stmt = access.interview_scope(current_user)
    if status := request.args.get("status"):
        stmt = stmt.where(Interview.status == status)
    return paged(stmt.order_by(Interview.scheduled_at), S.interview_json)


@bp.post("/applications/<int:app_id>/interviews")
@permission_required("interview.schedule")
def create_interview(app_id):
    app = _app_visible(app_id)
    interview = ivs.schedule_interview(app, InterviewIn.model_validate(body()), current_user)
    return created(S.interview_json(interview))


def _iv_visible(interview_id) -> Interview:
    iv = access.get_or_404(Interview, interview_id, "Interview not found.")
    if not access.can_view_interview(current_user, iv):
        raise NotFound("Interview not found.")
    return iv


@bp.get("/interviews/<int:interview_id>")
@permission_required("interview.view")
def get_interview(interview_id):
    iv = _iv_visible(interview_id)
    data = S.interview_json(iv)
    if access.can_view_all_feedback(current_user, iv):
        data["feedback"] = [S.feedback_json(f) for f in iv.feedback]
    else:
        data["feedback"] = [S.feedback_json(f) for f in iv.feedback if f.interviewer_id == current_user.id]
    return jsonify(data)


@bp.post("/interviews/<int:interview_id>/feedback")
@permission_required("interview.feedback")
def interview_feedback(interview_id):
    iv = _iv_visible(interview_id)
    fb = ivs.submit_feedback(iv, FeedbackIn.model_validate(body()), current_user)
    return created(S.feedback_json(fb))


@bp.post("/interviews/<int:interview_id>/status")
@permission_required("interview.view")
def interview_status(interview_id):
    iv = _iv_visible(interview_id)
    data = InterviewStatusIn.model_validate(body())
    ivs.set_interview_status(iv, data.status, data.reason, current_user)
    return ok(S.interview_json(iv))


@bp.post("/interviews/<int:interview_id>/reschedule")
@permission_required("interview.schedule")
def interview_reschedule(interview_id):
    iv = _iv_visible(interview_id)
    ivs.reschedule_interview(iv, RescheduleIn.model_validate(body()), current_user)
    return ok(S.interview_json(iv))


@bp.post("/interviews/<int:interview_id>/questions")
@permission_required("interview.view")
@rate_limit("20/60")
def interview_questions(interview_id):
    iv = _iv_visible(interview_id)
    questions = ivs.generate_questions(iv, current_user)
    return ok({"questions": questions, "ai_generated": True})


# ---------------------------------------------------------------------- offers
@bp.get("/offers")
@login_required_json
def list_offers():
    staff = current_user.can("offer.view")
    stmt = access.offer_scope(current_user)
    return paged(stmt.order_by(Offer.updated_at.desc()), lambda o: S.offer_json(o, staff))


@bp.post("/applications/<int:app_id>/offers")
@permission_required("offer.create")
def create_offer(app_id):
    app = _app_visible(app_id)
    offer = offer_service.create_offer(app, OfferIn.model_validate(body()), current_user)
    return created(S.offer_json(offer))


def _offer_visible(offer_id) -> Offer:
    offer = access.get_or_404(Offer, offer_id, "Offer not found.")
    if not access.can_view_offer(current_user, offer):
        raise NotFound("Offer not found.")
    return offer


@bp.get("/offers/<int:offer_id>")
@login_required_json
def get_offer(offer_id):
    offer = _offer_visible(offer_id)
    return jsonify(S.offer_json(offer, staff=current_user.can("offer.view")))


@bp.patch("/offers/<int:offer_id>")
@permission_required("offer.create")
def update_offer(offer_id):
    offer = _offer_visible(offer_id)
    current = {"designation": offer.designation, "department_id": offer.department_id, "salary": str(offer.salary),
               "currency": offer.currency, "joining_date": offer.joining_date.isoformat(),
               "expires_on": offer.expires_on.isoformat() if offer.expires_on else None, "terms": offer.terms}
    offer_service.update_offer(offer, OfferIn.model_validate({**current, **body()}), current_user)
    return ok(S.offer_json(offer))


@bp.post("/offers/<int:offer_id>/submit")
@permission_required("offer.create")
def submit_offer(offer_id):
    offer = _offer_visible(offer_id)
    offer_service.submit_offer(offer, current_user)
    return ok(S.offer_json(offer))


@bp.post("/offers/<int:offer_id>/decision")
@permission_required("offer.approve")
def offer_decision(offer_id):
    offer = _offer_visible(offer_id)
    data = OfferDecisionIn.model_validate(body())
    offer_service.decide_offer(offer, data.decision, data.comments, current_user)
    return ok(S.offer_json(offer))


@bp.post("/offers/<int:offer_id>/withdraw")
@permission_required("offer.approve")
def offer_withdraw(offer_id):
    offer = _offer_visible(offer_id)
    reason = (body().get("reason") or "").strip()
    if len(reason) < 5:
        raise ValidationFailed("Provide a reason for withdrawing the offer.")
    offer_service.withdraw_offer(offer, reason[:1000], current_user)
    return ok(S.offer_json(offer))


@bp.post("/offers/<int:offer_id>/respond")
@permission_required("self.candidate")
def offer_respond(offer_id):
    data = OfferResponseIn.model_validate(body())
    offer, employee = offer_service.respond_to_offer(offer_id, data.response, data.reason, current_user)
    return ok({"offer": S.offer_json(offer, staff=False),
               "employee": S.employee_json(employee) if employee is not None else None})


# ------------------------------------------------------------------- employees
@bp.get("/employees")
@permission_required("employee.view")
def list_employees():
    return paged(access.employee_scope(current_user).order_by(sa.text("employees.id desc")), S.employee_json)


@bp.get("/employees/me")
@login_required_json
def my_employee():
    emp = access.current_employee(current_user)
    if emp is None:
        raise NotFound("You do not have an employee record.")
    return jsonify(S.employee_json(emp))


def _emp_visible(employee_id):
    from app.models import Employee

    emp = access.get_or_404(Employee, employee_id, "Employee not found.")
    if not access.can_view_employee(current_user, emp):
        raise NotFound("Employee not found.")
    return emp


@bp.get("/employees/<int:employee_id>")
@login_required_json
def get_employee(employee_id):
    return jsonify(S.employee_json(_emp_visible(employee_id)))


@bp.get("/employees/<int:employee_id>/documents")
@login_required_json
def list_documents(employee_id):
    emp = _emp_visible(employee_id)
    return jsonify(items=[S.document_json(d) for d in emp.documents])


@bp.post("/documents/<int:doc_id>/upload")
@login_required_json
@rate_limit("30/600")
def upload_document(doc_id):
    doc = access.get_or_404(EmployeeDocument, doc_id, "Document not found.")
    obs.upload_document(doc, request.files.get("file"), current_user)
    return ok(S.document_json(doc))


@bp.post("/documents/<int:doc_id>/review")
@permission_required("onboarding.manage")
def review_document(doc_id):
    doc = access.get_or_404(EmployeeDocument, doc_id, "Document not found.")
    data = DocumentReviewIn.model_validate(body())
    obs.review_document(doc, data.decision, data.notes, current_user)
    return ok(S.document_json(doc))


# ------------------------------------------------------------------ onboarding
@bp.get("/onboarding/tasks")
@login_required_json
def list_tasks():
    from app.models import Employee

    stmt = sa.select(OnboardingTask).join(Employee)
    if not current_user.can("onboarding.manage"):
        stmt = stmt.where(sa.or_(Employee.user_id == current_user.id, Employee.manager_id == current_user.id,
                                 OnboardingTask.assignee_user_id == current_user.id))
    if employee_id := request.args.get("employee_id", type=int):
        stmt = stmt.where(OnboardingTask.employee_id == employee_id)
    if status := request.args.get("status"):
        stmt = stmt.where(OnboardingTask.status == status)
    return paged(stmt.order_by(OnboardingTask.due_date), S.task_json)


@bp.post("/onboarding/tasks")
@login_required_json
def create_task():
    data = TaskIn.model_validate(body())
    emp = _emp_visible(data.employee_id)
    task = obs.create_task(emp, data, current_user)
    return created(S.task_json(task))


@bp.patch("/onboarding/tasks/<int:task_id>")
@login_required_json
def update_task(task_id):
    task = access.get_or_404(OnboardingTask, task_id, "Task not found.")
    if not obs.can_update_task(current_user, task):
        raise NotFound("Task not found.")
    data = TaskStatusIn.model_validate(body())
    obs.update_task_status(task, data.status, data.blocked_reason, current_user)
    return ok(S.task_json(task))


@bp.get("/training")
@login_required_json
def list_training():
    from app.models import Employee

    stmt = sa.select(TrainingAssignment).join(Employee)
    if not current_user.can("onboarding.manage"):
        stmt = stmt.where(sa.or_(Employee.user_id == current_user.id, Employee.manager_id == current_user.id))
    return paged(stmt.order_by(TrainingAssignment.due_date), S.training_json)


@bp.patch("/training/<int:training_id>")
@login_required_json
def update_training(training_id):
    t = access.get_or_404(TrainingAssignment, training_id, "Training not found.")
    obs.update_training(t, TrainingStatusIn.model_validate(body()).status, current_user)
    return ok(S.training_json(t))


# --------------------------------------------------------------- notifications
@bp.get("/notifications")
@login_required_json
def list_notifications():
    stmt = sa.select(Notification).where(Notification.user_id == current_user.id)
    if request.args.get("unread") == "1":
        stmt = stmt.where(Notification.read_at.is_(None))
    return paged(stmt.order_by(Notification.created_at.desc()), S.notification_json)


@bp.post("/notifications/<int:notification_id>/read")
@login_required_json
def read_notification(notification_id):
    if not mark_read(current_user.id, notification_id):
        n = db.session.get(Notification, notification_id)
        if n is None or n.user_id != current_user.id:
            raise NotFound("Notification not found.")
    return ok({"status": "read"})


# ------------------------------------------------------------- analytics / ai
@bp.get("/analytics/summary")
@permission_required("analytics.view")
def analytics_summary():
    from datetime import date

    def d(name):
        value = request.args.get(name)
        try:
            return date.fromisoformat(value) if value else None
        except ValueError as exc:
            raise ValidationFailed(f"{name} must be YYYY-MM-DD.") from exc

    return jsonify(compute_metrics(d("start"), d("end"), request.args.get("department_id", type=int)))


@bp.get("/ai/status")
@login_required_json
def ai_status():
    if not current_user.is_staff:
        raise Forbidden()
    status = get_ai().status()
    if not current_user.can("admin.ai"):
        status = {k: status[k] for k in ("enabled", "reachable", "model_available", "model")}
    return jsonify(status)

