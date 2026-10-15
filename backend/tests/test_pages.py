"""Every page renders for every role without a server error, and HR pages are permission-gated."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import OnboardingTemplate
from tests.conftest import login
from tests.test_workflow import run_full_workflow


def _pages(w):
    tpl = db.session.scalar(sa.select(OnboardingTemplate.id))
    pages = [
        "/", "/careers/", "/careers/apply", "/auth/login?as=employee", "/auth/login?as=candidate", f"/careers/{w['job'].id}", "/privacy", "/health", "/ready", "/dashboard", "/notifications",
        "/hr/hiring-requests", "/hr/jobs", "/hr/jobs/new", f"/hr/jobs/{w['job'].id}", f"/hr/jobs/{w['job'].id}/edit",
        "/hr/jobs/clusters", "/hr/candidates/", f"/hr/candidates/{w['candidate'].candidate.id}",
        "/hr/applications/", f"/hr/applications/{w['application'].id}", f"/hr/applications/by-job/{w['job'].id}",
        "/hr/interviews/", "/hr/interviews/?view=past", "/hr/interviews/?view=mine", f"/hr/interviews/{w['interview'].id}",
        "/hr/offers/", f"/hr/offers/{w['offer'].id}",
        "/hr/employees/", f"/hr/employees/{w['employee'].id}",
        "/hr/onboarding/tasks", "/hr/onboarding/tasks?overdue=1", "/hr/onboarding/templates",
        "/hr/onboarding/training", "/hr/onboarding/it-requests", "/hr/onboarding/probation", "/hr/onboarding/probation?show=all",
        "/hr/analytics/", "/hr/analytics/?days=30",
        "/admin/users", "/admin/departments", f"/admin/departments?edit={w['department'].id}", "/admin/ai",
        "/admin/audit", "/admin/settings",
        "/me/", "/me/applications", f"/me/applications/{w['application'].id}", "/me/resumes", "/me/profile",
        f"/me/offers/{w['offer'].id}", "/portal/onboarding", "/portal/profile", "/auth/password",
    ]
    if tpl:
        pages.append(f"/hr/onboarding/templates/{tpl}")
    return pages


def test_every_page_renders_for_every_role(client, factory):
    w = run_full_workflow(client, factory)
    admin = factory.user("super_admin", "root@example.test")
    users = [admin, *w["staff"].values(), w["candidate"], factory.user("candidate", "other@example.test")]
    pages = _pages(w)
    errors = []
    for user in users:
        login(client, user)
        for url in pages:
            resp = client.get(url)
            if resp.status_code >= 500:
                errors.append(f"{user.role} {url} -> {resp.status_code}")
    client.post("/auth/logout")
    for url in pages:
        if client.get(url).status_code >= 500:
            errors.append(f"anonymous {url}")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("url, allowed", [
    ("/admin/users", {"super_admin"}),
    ("/admin/audit", {"super_admin"}),
    ("/admin/settings", {"super_admin"}),
    ("/admin/departments", {"super_admin", "hr_manager"}),
    ("/admin/ai", {"super_admin", "hr_manager"}),
    ("/hr/analytics/", {"super_admin", "hr_manager"}),
    ("/hr/offers/", {"super_admin", "hr_manager", "recruiter", "hiring_manager"}),
    ("/hr/employees/", {"super_admin", "hr_manager", "hiring_manager"}),
    ("/hr/onboarding/tasks", {"super_admin", "hr_manager"}),
    ("/hr/onboarding/templates", {"super_admin", "hr_manager"}),
    ("/hr/jobs", {"super_admin", "hr_manager", "recruiter", "hiring_manager"}),
    ("/hr/candidates/", {"super_admin", "hr_manager", "recruiter", "hiring_manager"}),
    ("/hr/interviews/", {"super_admin", "hr_manager", "recruiter", "hiring_manager", "interviewer"}),
    ("/hr/jobs/clusters", {"super_admin", "hr_manager", "recruiter", "hiring_manager"}),
])
def test_role_permission_matrix(client, factory, url, allowed):
    for role in ("super_admin", "hr_manager", "recruiter", "hiring_manager", "interviewer", "candidate"):
        login(client, factory.user(role))
        code = client.get(url).status_code
        if role in allowed:
            assert code == 200, f"{role} should reach {url} (got {code})"
        else:
            assert code == 403, f"{role} must be refused {url} (got {code})"
    client.post("/auth/logout")
    assert client.get(url).status_code in (302, 401)  # anonymous -> sign in


@pytest.mark.parametrize("method, url, body", [
    ("post", "/api/v1/departments", {"name": "X Dept", "code": "XD"}),
    ("post", "/api/v1/jobs/clusters/recompute", None),
    ("get", "/api/v1/candidates", None),
    ("get", "/api/v1/employees", None),
    ("get", "/api/v1/analytics/summary", None),
    ("post", "/api/v1/candidates/1/job-comparison", {"job_ids": [1]}),
])
def test_candidates_are_refused_sensitive_api_endpoints(client, factory, method, url, body):
    login(client, factory.user("candidate"))
    resp = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "forbidden"


def test_api_requires_authentication(client):
    resp = client.get("/api/v1/applications")
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"]


def test_task_api_is_scoped_to_own_records(client, factory):
    w = run_full_workflow(client, factory)
    login(client, factory.user("candidate", "nosy@example.test"))
    assert client.get("/api/v1/onboarding/tasks").get_json()["items"] == []
    assert client.get(f"/api/v1/employees/{w['employee'].id}").status_code in (403, 404)
    login(client, w["candidate"])  # now an employee: sees only their own tasks
    items = client.get("/api/v1/onboarding/tasks").get_json()["items"]
    assert items and all(t["employee_id"] == w["employee"].id for t in items)


def test_public_entry_paths(client, factory):
    factory.job(title="Backend Engineer")
    home = client.get("/").get_data(as_text=True)
    assert "Apply for a job" in home and "Employee sign in" in home
    apply_page = client.get("/careers/apply").get_data(as_text=True)
    assert "How to apply" in apply_page and "Backend Engineer" in apply_page and "Create candidate account" in apply_page
    assert "Employee sign in" in client.get("/auth/login?as=employee").get_data(as_text=True)
    assert "Candidate sign in" in client.get("/auth/login?as=candidate").get_data(as_text=True)


def test_short_links(client):
    assert client.get("/apply").headers["Location"].endswith("/careers/apply")
    assert "as=employee" in client.get("/employee").headers["Location"]
