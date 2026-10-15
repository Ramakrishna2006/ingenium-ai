"""Uploads, authentication, CSRF, headers, health and migrations."""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

from app.extensions import db
from app.models import AuditLog, Resume, User
from tests.conftest import PASSWORD, RESUME_TEXT, docx_bytes, login, pdf_bytes

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # commands run from the project root, as in the README


# --------------------------------------------------------------------- uploads
def _upload(client, data: bytes, name: str):
    return client.post("/me/resumes", data={"resume": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def test_pdf_and_docx_resumes_are_parsed_and_stored_privately(client, factory, app):
    login(client, factory.user("candidate"))
    assert _upload(client, pdf_bytes(RESUME_TEXT), "cv.pdf").status_code == 302
    assert _upload(client, docx_bytes(RESUME_TEXT), "cv.docx").status_code == 302
    resumes = db.session.scalars(sa.select(Resume)).all()
    assert len(resumes) == 2 and all(r.parse_status == "parsed" for r in resumes)
    for r in resumes:
        assert r.stored_filename not in ("cv.pdf", "cv.docx")
        assert "static" not in app.config["UPLOAD_FOLDER"]
    assert client.get(f"/static/{resumes[0].stored_filename}").status_code == 404


def test_rejects_wrong_extension_fake_content_empty_and_oversize(client, factory, app):
    login(client, factory.user("candidate"))
    _upload(client, b"MZ\x90\x00 not a resume", "virus.exe")
    _upload(client, b"just text pretending", "fake.pdf")
    _upload(client, b"", "empty.docx")
    app.config["MAX_CONTENT_LENGTH"] = 1024
    big = _upload(client, b"%PDF-" + b"0" * 5000, "big.pdf")
    assert big.status_code in (302, 413)
    assert db.session.scalar(sa.select(sa.func.count(Resume.id))) == 0


def test_api_upload_error_is_json(client, factory):
    login(client, factory.user("candidate"))
    resp = client.post("/api/v1/candidates/me/resumes", data={"resume": (io.BytesIO(b"nope"), "x.pdf")},
                       content_type="multipart/form-data")
    assert resp.status_code == 422 and resp.get_json()["error"]["code"] == "validation_error"


# ------------------------------------------------------------------------ auth
def test_registration_password_rules(client):
    for weak in ("short1", "allletterslong", "1234567890", "password123"):
        resp = client.post("/auth/register", data={"full_name": "Weak User", "email": "weak@example.test",
                                                    "password": weak, "confirm_password": weak, "consent": "on"})
        assert resp.status_code in (302, 409, 422)
        assert db.session.scalar(sa.select(User).where(User.email == "weak@example.test")) is None


def test_api_register_ignores_role_and_rejects_duplicates(client):
    body = {"full_name": "Api User", "email": "api@example.test", "password": PASSWORD, "consent": True,
            "role": "super_admin"}
    assert client.post("/api/v1/auth/register", json=body).status_code == 201
    assert db.session.scalar(sa.select(User.role).where(User.email == "api@example.test")) == "candidate"
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/register", json=body).status_code == 409


def test_login_failures_and_deactivated_users(client, factory):
    user = factory.user("recruiter")
    assert client.post("/auth/login", data={"email": user.email, "password": "wrong-pass-123"}).status_code == 401
    assert client.post("/auth/login", data={"email": "nobody@example.test", "password": "wrong-pass-123"}).status_code == 401
    user.active = False
    db.session.commit()
    assert client.post("/auth/login", data={"email": user.email, "password": PASSWORD}).status_code == 401
    assert "auth.login_failed" in db.session.scalars(sa.select(AuditLog.action)).all()


def test_role_change_invalidates_existing_sessions(client, factory):
    admin = factory.user("super_admin")
    victim = factory.user("hr_manager")
    login(client, victim)
    assert client.get("/hr/analytics/").status_code == 200
    admin_client = client.application.test_client()
    login(admin_client, admin)
    admin_client.post(f"/admin/users/{victim.id}", data={"role": "recruiter", "active": "on"})
    db.session.refresh(victim)
    assert victim.role == "recruiter"
    assert client.get("/hr/analytics/").status_code in (302, 401)  # old session no longer valid


def test_admin_cannot_remove_last_super_admin(client, factory):
    admin = factory.user("super_admin")
    login(client, admin)
    client.post(f"/admin/users/{admin.id}", data={"role": "recruiter", "active": "on"})
    db.session.refresh(admin)
    assert admin.role == "super_admin"


def test_csrf_is_enforced_when_enabled(app, factory):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    resp = client.post("/auth/login", data={"email": "x@example.test", "password": "whatever-123"})
    assert resp.status_code in (400, 302)
    assert b"csrf" in resp.data.lower() or resp.status_code == 302
    api = client.post("/api/v1/auth/login", json={"email": "x@example.test", "password": "whatever-123"})
    assert api.status_code == 400 and api.get_json()["error"]["code"] == "csrf_failed"


def test_login_rate_limit(app, factory):
    app.config["RATELIMIT_ENABLED"] = True
    client = app.test_client()
    codes = [client.post("/auth/login", data={"email": "rl@example.test", "password": "bad-pass-123"}).status_code
             for _ in range(12)]
    assert 429 in codes


# ------------------------------------------------------------- platform basics
def test_health_ready_and_security_headers(client):
    health = client.get("/health")
    assert health.status_code == 200 and health.get_json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200 and ready.get_json()["checks"]["database"] == "ok"
    assert client.get("/api/v1/health").status_code == 200
    page = client.get("/")
    for header in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert header in page.headers


def test_api_errors_are_consistent_json(client, factory):
    login(client, factory.user("hr_manager"))
    nf = client.get("/api/v1/jobs/999999")
    assert nf.status_code == 404 and set(nf.get_json()["error"]) >= {"code", "message"}
    bad = client.post("/api/v1/jobs", json={"title": "x"})
    assert bad.status_code == 422 and bad.get_json()["error"]["details"]
    assert client.get("/api/v1/does-not-exist").status_code == 404


def test_production_config_refuses_weak_secret(monkeypatch):
    from app import create_app

    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    import importlib

    import config
    importlib.reload(config)
    try:
        create_app("production")
    except RuntimeError as exc:
        assert "SECRET_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("weak production SECRET_KEY was accepted")
    finally:
        monkeypatch.delenv("SECRET_KEY")
        monkeypatch.delenv("DATABASE_URL")
        importlib.reload(config)


def test_seed_demo_refuses_production(app):
    from app.cli import seed_demo

    app.config["APP_ENV"] = "production"
    result = app.test_cli_runner().invoke(seed_demo)
    assert result.exit_code != 0 and "disabled in production" in result.output


# ------------------------------------------------------------------ migrations
def test_migrations_build_schema_matching_models(tmp_path):
    """`flask db upgrade` on an empty database, then `flask db check` finds no drift."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'migrate.sqlite3'}"
    if os.environ.get("TEST_DATABASE_URL"):
        import pytest
        pytest.skip("migration test uses its own throwaway SQLite database")
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "development", "SECRET_KEY": "x" * 40}
    for cmd in (["db", "upgrade"], ["db", "check"]):
        proc = subprocess.run([sys.executable, "-m", "flask", "--app", "backend/run.py", *cmd], cwd=PROJECT_ROOT, env=env,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run([sys.executable, "-m", "flask", "--app", "backend/run.py", "db", "downgrade", "base"], cwd=PROJECT_ROOT,
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
