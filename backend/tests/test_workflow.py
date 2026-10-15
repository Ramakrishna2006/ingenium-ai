"""End-to-end acceptance workflow (prompt section 17) driven through the real HTML routes."""
from __future__ import annotations

import io
from datetime import timedelta

import sqlalchemy as sa

from app.extensions import db
from app.models import (
    Application, AuditLog, Department, Employee, Interview, Job, Notification, Offer, OnboardingTask, Resume, User,
    utcnow,
)
from tests.conftest import PASSWORD, RESUME_TEXT, docx_bytes, future, login


def _staff(factory):
    return {
        "hr": factory.user("hr_manager", "hr@example.test", "Harini HR"),
        "recruiter": factory.user("recruiter", "rec@example.test", "Rohan Recruiter"),
        "manager": factory.user("hiring_manager", "mgr@example.test", "Meera Manager"),
        "interviewer": factory.user("interviewer", "iv@example.test", "Ishaan Interviewer"),
    }


def test_complete_recruitment_and_onboarding_workflow(client, factory):
    run_full_workflow(client, factory)


def run_full_workflow(client, factory) -> dict:
    """Drive the whole hiring lifecycle; returns the key records for reuse by other tests."""
    s = _staff(factory)

    # 1. HR creates a department
    login(client, s["hr"])
    r = client.post("/admin/departments", data={"name": "Engineering", "code": "eng", "is_active": "on"})
    assert r.status_code == 302
    dept = db.session.scalar(sa.select(Department).where(Department.code == "ENG"))
    assert dept is not None

    # 2. HR creates and publishes multiple jobs
    job_specs = [
        ("Backend Engineer", "Python, Flask, PostgreSQL, Docker", "Build REST APIs in Python and Flask on PostgreSQL."),
        ("Data Scientist", "Python, Machine Learning, Statistics", "Train machine learning models and analyse data."),
        ("DevOps Engineer", "AWS, Kubernetes, Terraform", "Operate Kubernetes clusters on AWS with Terraform."),
    ]
    for title, skills, desc in job_specs:
        r = client.post("/hr/jobs/new", data={
            "title": title, "department_id": dept.id, "description": desc + " Collaborate with the team and ship.",
            "required_skills": skills, "employment_type": "full_time", "location": "Bengaluru",
            "recruiter_id": s["recruiter"].id, "hiring_manager_id": s["manager"].id, "publish": "1"})
        assert r.status_code == 302, r.data[:300]
    jobs = db.session.scalars(sa.select(Job).order_by(Job.id)).all()
    assert len(jobs) == 3 and all(j.status == "published" for j in jobs)
    backend = jobs[0]
    assert client.get("/careers/").status_code == 200

    # 3. Candidate registers and signs in (public sign-up cannot pick a role)
    client.post("/auth/logout")
    r = client.post("/auth/register", data={"full_name": "Priya Candidate", "email": "priya@example.test",
                                            "password": PASSWORD, "confirm_password": PASSWORD, "consent": "on",
                                            "role": "super_admin"})
    assert r.status_code == 302
    priya = db.session.scalar(sa.select(User).where(User.email == "priya@example.test"))
    assert priya.role == "candidate"

    # 4. Candidate uploads a resume and applies
    r = client.post("/me/resumes", data={"resume": (io.BytesIO(docx_bytes(RESUME_TEXT)), "priya_cv.docx")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    resume = db.session.scalar(sa.select(Resume).where(Resume.candidate_id == priya.candidate.id))
    assert resume is not None and resume.stored_filename != "priya_cv.docx"
    assert resume.parse_status == "parsed"
    assert "python" in [x.lower() for x in resume.extracted.get("skills", [])]
    r = client.post(f"/careers/{backend.id}/apply", data={"resume_id": resume.id, "cover_letter": "Keen to join."})
    assert r.status_code == 302
    app_ = db.session.scalar(sa.select(Application).where(Application.candidate_id == priya.candidate.id))
    assert app_.status == "submitted"

    # 5. Duplicate application is prevented
    r = client.post(f"/careers/{backend.id}/apply", data={"resume_id": resume.id})
    assert r.status_code in (302, 409)
    assert db.session.scalar(sa.select(sa.func.count(Application.id)).where(
        Application.candidate_id == priya.candidate.id, Application.job_id == backend.id)) == 1
    api = client.post("/api/v1/applications", json={"job_id": backend.id})
    assert api.status_code == 409

    # 6-7. Recruiter reviews, screens (AI offline in tests -> rule-based evidence still produced)
    login(client, s["recruiter"])
    assert client.get(f"/hr/applications/{app_.id}").status_code == 200
    r = client.post(f"/hr/applications/{app_.id}/screen", data={"use_ai": "1"})
    assert r.status_code == 302
    db.session.refresh(app_)
    screening = app_.latest_screening
    assert screening is not None and screening.skill_overlap is not None
    assert screening.ai_summary is None and screening.ai_error  # AI unavailable, recorded honestly
    for status in ("under_review", "shortlisted"):
        assert client.post(f"/hr/applications/{app_.id}/status", data={"status": status}).status_code == 302
    db.session.refresh(app_)
    assert app_.status == "shortlisted"

    # 8. HR views clusters and similar jobs
    login(client, s["hr"])
    assert client.post("/hr/jobs/clusters/recompute").status_code == 302
    assert client.get("/hr/jobs/clusters").status_code == 200
    clusters = client.get("/api/v1/jobs/clusters").get_json()
    assert clusters["run"]["status"] in ("completed", "insufficient_data")
    assert client.get(f"/api/v1/jobs/{backend.id}/similar").status_code == 200

    # 9. Recruiter schedules an interview (scheduling in the past is refused)
    login(client, s["recruiter"])
    past = (utcnow() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    client.post(f"/hr/interviews/new/{app_.id}", data={
        "kind": "technical", "scheduled_local": past, "timezone": "UTC", "duration_minutes": "45",
        "meeting_link": "https://meet.example.com/abc", "interviewer_ids": [str(s["interviewer"].id)]})
    assert db.session.scalar(sa.select(sa.func.count(Interview.id))) == 0
    when = (utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    r = client.post(f"/hr/interviews/new/{app_.id}", data={
        "kind": "technical", "scheduled_local": when, "timezone": "UTC", "duration_minutes": "45",
        "meeting_link": "https://meet.example.com/abc", "interviewer_ids": [str(s["interviewer"].id)]})
    assert r.status_code == 302, r.data[:300]
    iv = db.session.scalar(sa.select(Interview).where(Interview.application_id == app_.id))
    assert iv is not None
    db.session.refresh(app_)
    assert app_.status == "interviewing"
    iv.scheduled_at = utcnow() - timedelta(hours=1)  # simulate the interview taking place
    db.session.commit()

    # 10. Interviewer submits structured feedback; cannot see candidate contact details
    login(client, s["interviewer"])
    page = client.get(f"/hr/interviews/{iv.id}")
    assert page.status_code == 200 and b"priya@example.test" not in page.data
    assert client.get(f"/hr/applications/{app_.id}").status_code in (403, 200)
    r = client.post(f"/hr/interviews/{iv.id}/feedback", data={
        "rating_technical": "4", "rating_communication": "5", "overall_rating": "4", "recommendation": "hire",
        "strengths": "Clear API design."})
    assert r.status_code == 302
    assert client.post(f"/hr/interviews/{iv.id}/status", data={"status": "completed"}).status_code == 302

    # 11. Hiring manager records the final decision
    login(client, s["manager"])
    r = client.post(f"/hr/applications/{app_.id}/decision", data={
        "decision": "approve", "rationale": "Strong technical interview and evidenced Flask experience."})
    assert r.status_code == 302
    db.session.refresh(app_)
    assert app_.status == "approved_for_offer"

    # 12. HR creates and approves an offer
    login(client, s["hr"])
    assert client.get(f"/hr/offers/new/{app_.id}").status_code == 200
    r = client.post(f"/hr/offers/new/{app_.id}", data={
        "designation": "Backend Engineer", "department_id": dept.id, "salary": "1800000", "currency": "inr",
        "joining_date": future(20), "terms": "Probation of 90 days.", "submit_for_approval": "1"})
    assert r.status_code == 302, r.data[:300]
    offer = db.session.scalar(sa.select(Offer).where(Offer.application_id == app_.id))
    assert offer.status == "pending_approval" and offer.currency == "INR"
    r = client.post(f"/hr/offers/{offer.id}/decision", data={"decision": "approved", "comments": "OK"})
    assert r.status_code == 302
    db.session.refresh(offer)
    assert offer.status == "approved" and offer.pdf_stored_filename
    assert client.get(f"/files/offers/{offer.id}").status_code == 200

    # 13-14. Candidate accepts; exactly one employee even with repeated requests
    login(client, priya)
    assert client.get(f"/me/offers/{offer.id}").status_code == 200
    assert client.post(f"/me/offers/{offer.id}/respond", data={"response": "accept"}).status_code == 302
    client.post(f"/me/offers/{offer.id}/respond", data={"response": "accept"})
    client.post(f"/api/v1/offers/{offer.id}/respond", json={"response": "accept"})
    employees = db.session.scalars(sa.select(Employee)).all()
    assert len(employees) == 1
    emp = employees[0]
    db.session.refresh(priya)
    assert priya.role == "employee" and emp.application_id == app_.id and emp.employee_code.startswith("IN")

    # 15. Onboarding tasks and required documents exist
    assert emp.tasks and emp.milestones
    assert db.session.scalar(sa.select(sa.func.count(OnboardingTask.id))) == len(emp.tasks)

    # 16. HR assigns an extra onboarding task to the employee
    login(client, s["hr"])
    assert client.get(f"/hr/employees/{emp.id}").status_code == 200
    r = client.post(f"/hr/employees/{emp.id}/tasks", data={"title": "Read the engineering handbook", "category": "orientation",
                                                           "assignee_type": "employee", "priority": "low", "due_date": future(5)})
    assert r.status_code == 302
    task = db.session.scalar(sa.select(OnboardingTask).where(OnboardingTask.title == "Read the engineering handbook"))

    # 17. Employee completes it in the portal
    login(client, priya)
    assert client.get("/portal/onboarding").status_code == 200
    assert client.post(f"/hr/employees/tasks/{task.id}/status", data={"status": "completed"}).status_code == 302
    db.session.refresh(task)
    assert task.status == "completed" and task.completed_at is not None
    # ...but cannot open HR pages
    assert client.get("/hr/employees/").status_code == 403

    # 18. Dashboard metrics reflect the hire
    login(client, s["hr"])
    assert client.get("/hr/analytics/").status_code == 200
    metrics = client.get("/api/v1/analytics/summary").get_json()
    assert metrics["applications"]["total"] == 1
    assert metrics["offers"]["acceptance_rate"] == 100.0
    assert metrics["time_to_hire"]["hires"] == 1
    assert metrics["onboarding"]["total_tasks"] >= 1

    # Audit trail covers the sensitive steps
    actions = set(db.session.scalars(sa.select(AuditLog.action)).all())
    for expected in ("auth.register", "offer.accepted", "employee.create"):
        assert expected in actions
    # Candidate was notified along the way
    assert db.session.scalar(sa.select(sa.func.count(Notification.id)).where(Notification.user_id == priya.id)) >= 3
    return {"staff": s, "candidate": priya, "employee": emp, "application": app_, "offer": offer, "interview": iv,
            "job": backend, "department": dept, "resume": resume, "task": task}


def test_candidate_cannot_access_other_candidates_records(client, factory):
    job = factory.job()
    alice = factory.user("candidate", "alice@example.test")
    bob = factory.user("candidate", "bob@example.test")

    login(client, alice)
    client.post("/me/resumes", data={"resume": (io.BytesIO(docx_bytes(RESUME_TEXT)), "a.docx")},
                content_type="multipart/form-data")
    client.post(f"/careers/{job.id}/apply", data={})
    a_app = db.session.scalar(sa.select(Application))
    a_resume = db.session.scalar(sa.select(Resume))

    login(client, bob)
    assert client.get(f"/me/applications/{a_app.id}").status_code in (403, 404)
    assert client.get(f"/files/resumes/{a_resume.id}").status_code in (403, 404)
    assert client.get(f"/api/v1/applications/{a_app.id}").status_code in (403, 404)
    assert client.get(f"/api/v1/candidates/{alice.candidate.id}").status_code == 403
    assert client.get("/hr/applications/").status_code == 403
    r = client.post(f"/me/resumes/{a_resume.id}/delete")  # HTML forms flash the error and redirect back
    assert r.status_code in (302, 403, 404)
    assert db.session.get(Resume, a_resume.id) is not None


def test_withdrawal_and_invalid_transition(client, factory):
    rec = factory.user("recruiter")
    job = factory.job(recruiter=rec)
    cand = factory.user("candidate")
    login(client, cand)
    assert client.post(f"/careers/{job.id}/apply", data={}).status_code == 302  # refused: no resume yet
    assert db.session.scalar(sa.select(sa.func.count(Application.id))) == 0
    client.post("/me/resumes", data={"resume": (io.BytesIO(docx_bytes(RESUME_TEXT)), "c.docx")},
                content_type="multipart/form-data")
    client.post(f"/careers/{job.id}/apply", data={})
    application = db.session.scalar(sa.select(Application))

    login(client, rec)
    # "interviewing" is set by scheduling, never manually
    assert client.post(f"/api/v1/applications/{application.id}/status", json={"status": "interviewing"}).status_code == 422
    assert client.post(f"/api/v1/applications/{application.id}/status", json={"status": "rejected"}).status_code == 422  # reason required
    assert client.post(f"/api/v1/applications/{application.id}/status", json={"status": "shortlisted"}).status_code == 200
    # shortlisted -> under_review is not an allowed transition
    assert client.post(f"/api/v1/applications/{application.id}/status", json={"status": "under_review"}).status_code == 409

    login(client, cand)
    assert client.post(f"/me/applications/{application.id}/withdraw", data={"reason": "Accepted elsewhere"}).status_code == 302
    db.session.refresh(application)
    assert application.status == "withdrawn"
    assert client.post(f"/me/applications/{application.id}/withdraw").status_code in (302, 409)


def test_assigned_interviewer_can_open_resume_but_not_others(client, factory):
    w = run_full_workflow(client, factory)
    sai = w["staff"]["interviewer"]
    other = factory.user("candidate", "other-cand@example.test")
    login(client, other)
    client.post("/me/resumes", data={"resume": (io.BytesIO(docx_bytes(RESUME_TEXT)), "o.docx")},
                content_type="multipart/form-data")
    other_resume = db.session.scalar(sa.select(Resume).where(Resume.candidate_id == other.candidate.id))
    login(client, sai)
    assert client.get(f"/files/resumes/{w['resume'].id}").status_code == 200       # assigned interview
    assert client.get(f"/files/resumes/{other_resume.id}").status_code == 403       # not assigned
    unassigned = factory.user("interviewer", "iv2@example.test")
    login(client, unassigned)
    assert client.get(f"/files/resumes/{w['resume'].id}").status_code == 403
