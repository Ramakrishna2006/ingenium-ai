"""Flask CLI commands: admin bootstrap, demo seed data and maintenance jobs.

    flask --app backend/run.py create-admin --email admin@example.com --name "Ada Admin"
    flask --app backend/run.py seed-demo [--with-users]
    flask --app backend/run.py recompute-clusters | send-reminders | expire-offers | purge-retention | check-ai
"""
from __future__ import annotations

import os
import secrets
from datetime import timedelta

import click
import sqlalchemy as sa
from flask import current_app

from app.extensions import db


def register_cli(app):
    app.cli.add_command(create_admin)
    app.cli.add_command(seed_demo)
    app.cli.add_command(recompute_clusters_cmd)
    app.cli.add_command(send_reminders_cmd)
    app.cli.add_command(expire_offers_cmd)
    app.cli.add_command(purge_retention_cmd)
    app.cli.add_command(check_ai_cmd)


@click.command("create-admin")
@click.option("--email", required=True, help="Administrator email address.")
@click.option("--name", "full_name", required=True, help="Administrator full name.")
@click.option("--password-env", default="INGENIUM_ADMIN_PASSWORD", show_default=True,
              help="Environment variable to read the password from (for non-interactive use).")
def create_admin(email, full_name, password_env):
    """Create (or promote) a Super Admin. The password is prompted securely or read from an env var."""
    from app.audit import record_audit
    from app.models import User
    from app.security import password_problems

    email = email.strip().lower()
    password = os.environ.get(password_env)
    if not password:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    problems = password_problems(password, email)
    if problems:
        raise click.ClickException("Password must have " + ", ".join(problems) + ".")
    user = db.session.scalar(sa.select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name=full_name, role="super_admin")
        db.session.add(user)
        action = "created"
    else:
        user.role = "super_admin"
        user.active = True
        user.session_version += 1
        action = "promoted"
    user.set_password(password)
    db.session.flush()
    record_audit("admin.bootstrap", "user", user.id, {"action": action}, actor=user)
    db.session.commit()
    click.echo(f"Super Admin {email} {action}.")


DEMO_DEPARTMENTS = [
    ("Engineering", "ENG", "Bengaluru"), ("Data & AI", "DATA", "Hyderabad"), ("Infrastructure & Security", "INFRA", "Pune"),
    ("Human Resources", "HR", "Chennai"), ("Finance & Operations", "FIN", "Mumbai"),
]

DEMO_JOBS = [
    ("Backend Engineer (Python)", "ENG", ["Python", "Flask", "PostgreSQL", "REST APIs", "Docker"],
     "Design and build REST APIs and backend services in Python and Flask. Model data in PostgreSQL, write tests and deploy containerised services.", 2),
    ("Senior Java Developer", "ENG", ["Java", "Spring Boot", "SQL", "Microservices", "Git"],
     "Develop Spring Boot microservices, optimise SQL queries and mentor engineers on backend design and code quality.", 5),
    ("Frontend Developer", "ENG", ["JavaScript", "React", "HTML", "CSS", "TypeScript"],
     "Build accessible, responsive web interfaces with React and TypeScript. Collaborate with designers on UI components and front-end performance.", 2),
    ("Full-Stack Developer", "ENG", ["JavaScript", "Node.js", "React", "MongoDB", "REST APIs"],
     "Own features end to end across a Node.js API and React front end backed by MongoDB.", 3),
    ("Data Analyst", "DATA", ["SQL", "Excel", "Power BI", "Data Analysis", "Statistics"],
     "Analyse business data with SQL, build Power BI dashboards and present statistical insights to stakeholders.", 1),
    ("Data Scientist", "DATA", ["Python", "Machine Learning", "Statistics", "Pandas", "scikit-learn"],
     "Build and evaluate machine learning models with Python, pandas and scikit-learn; run experiments and communicate statistical results.", 3),
    ("Machine Learning Engineer (LLMs)", "DATA", ["Python", "Deep Learning", "PyTorch", "NLP", "LLMs"],
     "Fine-tune and serve deep learning and NLP models, evaluate large language models and build ML pipelines in PyTorch.", 3),
    ("DevOps Engineer", "INFRA", ["AWS", "Docker", "Kubernetes", "Terraform", "CI/CD"],
     "Automate cloud infrastructure on AWS with Terraform, run Kubernetes clusters and maintain CI/CD pipelines.", 3),
    ("Cloud Platform Engineer", "INFRA", ["Azure", "Kubernetes", "Linux", "Terraform", "Networking"],
     "Operate the Azure cloud platform, Kubernetes and Linux fleet; manage infrastructure as code with Terraform.", 4),
    ("Security Analyst", "INFRA", ["Cybersecurity", "SIEM", "Networking", "Firewalls", "Penetration Testing"],
     "Monitor security events in the SIEM, investigate incidents, harden firewalls and run penetration tests.", 2),
    ("HR Generalist", "HR", ["HR Operations", "Recruitment", "Onboarding", "Labour Law", "Communication"],
     "Run HR operations including recruitment coordination, onboarding, employee relations and labour-law compliance.", 2),
    ("Talent Acquisition Specialist", "HR", ["Recruitment", "Communication", "Onboarding", "Excel"],
     "Source and screen candidates, coordinate interviews and manage offers and onboarding handoffs.", 1),
    ("Financial Analyst", "FIN", ["Financial Analysis", "Excel", "Accounting", "Power BI"],
     "Prepare budgets and forecasts, build financial models in Excel and report on business performance.", 2),
    ("Operations Associate", "FIN", ["Operations", "Excel", "Communication", "Project Management"],
     "Coordinate day-to-day operations, supply chain logistics and process improvement projects.", 0),
]

DEMO_TEMPLATE_ITEMS = [
    ("Upload government photo ID", "document", "employee", -3, "high", "id_proof"),
    ("Upload signed offer letter", "document", "employee", -3, "high", "signed_offer"),
    ("Upload educational certificates", "document", "employee", 0, "medium", "education_certificates"),
    ("Acknowledge code of conduct", "policy", "employee", 1, "high", None),
    ("Acknowledge acceptable use & security policy", "policy", "employee", 1, "high", None),
    ("Send joining instructions and first-day agenda", "pre_boarding", "hr", -5, "high", None),
    ("Laptop with development tools", "it", "it", -1, "high", None),
    ("Email, Git and VPN accounts", "it", "it", -1, "high", None),
    ("Day-one orientation", "orientation", "hr", 0, "medium", None),
    ("Assign onboarding buddy", "manager", "manager", 0, "medium", None),
    ("Set 30/60/90-day goals with manager", "manager", "manager", 7, "medium", None),
    ("Secure coding fundamentals", "training", "employee", 14, "medium", None),
    ("Engineering handbook walkthrough", "orientation", "employee", 3, "low", None),
]


@click.command("seed-demo")
@click.option("--with-users", is_flag=True, help="Also create one demo account per staff role (random passwords printed once).")
def seed_demo(with_users):
    """Add demo departments, vacancies and onboarding templates. Refuses to run in production."""
    from app.models import Department, Job, OnboardingTemplate, OnboardingTemplateItem, User, utcnow

    if current_app.config["APP_ENV"] == "production" or os.environ.get("APP_ENV") == "production":
        raise click.ClickException("seed-demo is disabled in production to avoid inserting fake records.")
    created = {"departments": 0, "jobs": 0, "templates": 0, "users": 0}
    depts = {}
    for name, code, location in DEMO_DEPARTMENTS:
        dept = db.session.scalar(sa.select(Department).where(Department.code == code))
        if dept is None:
            dept = Department(name=name, code=code, location=location, description=f"{name} department")
            db.session.add(dept)
            created["departments"] += 1
        depts[code] = dept
    db.session.flush()

    users = {}
    if with_users:
        click.echo("Demo accounts (store these now; passwords are not shown again):")
        for role, email, name in [("hr_manager", "hr@demo.ingenium.local", "Chandu (HR Manager)"),
                                  ("recruiter", "recruiter@demo.ingenium.local", "Susmita (Recruiter)"),
                                  ("hiring_manager", "manager@demo.ingenium.local", "Amar (Hiring Manager)"),
                                  ("interviewer", "interviewer@demo.ingenium.local", "Sai (Interviewer)")]:
            user = db.session.scalar(sa.select(User).where(User.email == email))
            if user is None:
                password = "Demo-" + secrets.token_urlsafe(9) + "7"
                user = User(email=email, full_name=name, role=role)
                user.set_password(password)
                db.session.add(user)
                created["users"] += 1
                click.echo(f"  {role:15} {email:38} {password}")
            users[role] = user
        db.session.flush()

    for title, code, skills, description, years in DEMO_JOBS:
        if db.session.scalar(sa.select(Job.id).where(Job.title == title)):
            continue
        job = Job(title=title, department_id=depts[code].id, description=description, required_skills=skills,
                  preferred_skills=["Communication"], experience_min_years=years, location=depts[code].location,
                  employment_type="internship" if years == 0 else "full_time", status="published",
                  published_at=utcnow(), application_deadline=(utcnow() + timedelta(days=60)).date(),
                  recruiter_id=getattr(users.get("recruiter"), "id", None),
                  hiring_manager_id=getattr(users.get("hiring_manager"), "id", None),
                  qualifications="Relevant degree or equivalent practical experience.")
        db.session.add(job)
        created["jobs"] += 1

    if not db.session.scalar(sa.select(OnboardingTemplate.id).where(OnboardingTemplate.name == "Engineering onboarding")):
        tpl = OnboardingTemplate(name="Engineering onboarding", department_id=depts["ENG"].id,
                                 description="Standard checklist for engineering hires.", is_default=False)
        for i, (title, cat, assignee, offset, prio, doc) in enumerate(DEMO_TEMPLATE_ITEMS):
            tpl.items.append(OnboardingTemplateItem(title=title, category=cat, assignee_type=assignee,
                                                    due_offset_days=offset, priority=prio, doc_type=doc, sort_order=i))
        db.session.add(tpl)
        created["templates"] += 1
    if not db.session.scalar(sa.select(OnboardingTemplate.id).where(OnboardingTemplate.name == "Company default onboarding")):
        tpl = OnboardingTemplate(name="Company default onboarding", is_default=True,
                                 description="Applies when no department-specific template matches.")
        for i, (title, cat, assignee, offset, prio, doc) in enumerate(DEMO_TEMPLATE_ITEMS):
            if cat in ("document", "policy", "pre_boarding", "orientation", "it"):
                tpl.items.append(OnboardingTemplateItem(title=title, category=cat, assignee_type=assignee,
                                                        due_offset_days=offset, priority=prio, doc_type=doc, sort_order=i))
        db.session.add(tpl)
        created["templates"] += 1
    db.session.commit()
    click.echo("Seeded: " + ", ".join(f"{v} {k}" for k, v in created.items()))
    click.echo("Run `flask --app backend/run.py recompute-clusters` to cluster the demo vacancies.")


@click.command("recompute-clusters")
def recompute_clusters_cmd():
    """Re-cluster published jobs (run after bulk job changes or on a schedule)."""
    from app.services.job_clustering import recompute_clusters

    run = recompute_clusters()
    db.session.commit()
    click.echo(f"Run {run.id}: {run.status} — {run.message}")


@click.command("send-reminders")
def send_reminders_cmd():
    """Notify assignees about overdue onboarding tasks (schedule daily)."""
    from app.services.onboarding_service import send_overdue_reminders

    with current_app.test_request_context():
        sent = send_overdue_reminders()
        db.session.commit()
    click.echo(f"{sent} reminder(s) sent.")


@click.command("expire-offers")
def expire_offers_cmd():
    """Mark extended offers past their expiry date as expired (schedule daily)."""
    from app.services.offer_service import expire_offers

    count = expire_offers()
    db.session.commit()
    click.echo(f"{count} offer(s) expired.")


@click.command("purge-retention")
@click.option("--dry-run", is_flag=True)
def purge_retention_cmd(dry_run):
    """Delete resumes of candidates whose applications all closed longer ago than the retention period."""
    from app.audit import record_audit
    from app.models import Application, Candidate, Employee, Resume, get_setting, utcnow
    from app.models.recruitment import APPLICATION_TERMINAL
    from app.services import storage

    days = int(get_setting("resume_retention_days") or 365)
    cutoff = utcnow() - timedelta(days=days)
    active = sa.select(Application.candidate_id).where(sa.or_(
        Application.status.not_in(APPLICATION_TERMINAL), Application.status_changed_at >= cutoff))
    employees = sa.select(Employee.candidate_id).where(Employee.candidate_id.is_not(None))
    resumes = db.session.scalars(sa.select(Resume).join(Candidate).where(
        Resume.created_at < cutoff, Resume.candidate_id.not_in(active), Resume.candidate_id.not_in(employees))).all()
    click.echo(f"{len(resumes)} resume(s) older than {days} days eligible for deletion.")
    if dry_run:
        return
    for r in resumes:
        storage.delete_file("resumes", r.stored_filename)
        record_audit("retention.purge", "resume", r.id, {"candidate_id": r.candidate_id}, actor=None)
        db.session.delete(r)
    db.session.commit()
    click.echo("Purge complete.")


@click.command("check-ai")
def check_ai_cmd():
    """Show Ollama connectivity and model availability."""
    from app.services.ai_service import get_ai

    with current_app.test_request_context():
        status = get_ai().status()
    for key, value in status.items():
        click.echo(f"{key:16}: {value}")
