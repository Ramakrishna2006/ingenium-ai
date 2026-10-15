"""Shared pytest fixtures.

Tests run against an in-memory SQLite database by default. To run them against a
separate PostgreSQL test database (never production!), set TEST_DATABASE_URL, e.g.

    TEST_DATABASE_URL=postgresql://ingenium:ingenium@localhost/ingenium_test pytest
"""
from __future__ import annotations

import io
import os
from datetime import date, timedelta

import pytest

os.environ.setdefault("APP_ENV", "testing")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

PASSWORD = "Sturdy-Pass-2026"


@pytest.fixture()
def app(tmp_path):
    test_db = os.environ.get("TEST_DATABASE_URL", "")
    if test_db and any(word in test_db for word in ("prod", "render.com")):
        pytest.exit("Refusing to run destructive tests against what looks like a production database.")
    application = create_app("testing")
    application.config.update(UPLOAD_FOLDER=str(tmp_path / "uploads"), SERVER_NAME="localhost")
    (tmp_path / "uploads").mkdir()

    @application.before_request
    def _fresh_login_state():
        # The fixture keeps one app context open, so Flask would reuse `g` across requests.
        # Production gets a fresh `g` per request; mimic that so the user is reloaded every time.
        from flask import g

        g.pop("_login_user", None)
    with application.app_context():
        db.drop_all()
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


# ----------------------------------------------------------------- factories
class Factory:
    def __init__(self, app):
        self.app = app
        self._n = 0

    def _next(self):
        self._n += 1
        return self._n

    def user(self, role="candidate", email=None, name=None, password=PASSWORD):
        from app.models import Candidate, User

        n = self._next()
        user = User(email=email or f"{role}{n}@example.test", full_name=name or f"{role.title()} Person {n}", role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        if role == "candidate":
            db.session.add(Candidate(user_id=user.id, skills=[], education=[], certifications=[]))
        db.session.commit()
        return user

    def department(self, name=None, code=None):
        from app.models import Department

        n = self._next()
        dept = Department(name=name or f"Department {n}", code=code or f"D{n}")
        db.session.add(dept)
        db.session.commit()
        return dept

    def job(self, dept=None, title="Backend Engineer", skills=("Python", "Flask", "PostgreSQL"), status="published",
            description=None, recruiter=None, manager=None):
        from app.models import Job, utcnow

        dept = dept or self.department()
        job = Job(title=title, department_id=dept.id, required_skills=list(skills), preferred_skills=[],
                  description=description or f"Build services for {title}. Work with {', '.join(skills)} daily.",
                  employment_type="full_time", status=status, published_at=utcnow() if status == "published" else None,
                  recruiter_id=getattr(recruiter, "id", None), hiring_manager_id=getattr(manager, "id", None),
                  location="Remote")
        db.session.add(job)
        db.session.commit()
        return job


@pytest.fixture()
def factory(app):
    return Factory(app)


def login(client, user_or_email, password=PASSWORD):
    email = getattr(user_or_email, "email", user_or_email)
    client.post("/auth/logout")
    resp = client.post("/auth/login", data={"email": email, "password": password})
    assert resp.status_code == 302, f"login failed for {email}: {resp.status_code}"
    return resp


def docx_bytes(text: str) -> bytes:
    import docx

    document = docx.Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def pdf_bytes(text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.splitlines():
        c.drawString(40, y, line)
        y -= 16
    c.save()
    return buf.getvalue()


RESUME_TEXT = """Priya Candidate
priya@example.test | +91 98765 43210
Backend developer with 4 years of experience building REST APIs in Python and Flask.
Designed PostgreSQL schemas, wrote pytest suites and deployed services with Docker.
Education: B.Tech Computer Science
Certifications: AWS Certified Cloud Practitioner
"""


def future(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()
