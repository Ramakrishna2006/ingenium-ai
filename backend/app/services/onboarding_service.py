"""Employee creation, pre-boarding/onboarding, documents, training, IT access and probation."""
from __future__ import annotations

import secrets
from datetime import timedelta

import sqlalchemy as sa
from flask import url_for

from app import access
from app.audit import record_audit
from app.errors import Conflict, Forbidden, InvalidTransition, ValidationFailed
from app.extensions import db
from app.models import (
    Employee, EmployeeDocument, ITAccessRequest, OnboardingTask, OnboardingTemplate, ProbationMilestone,
    TrainingAssignment, User, get_setting, today_utc, utcnow,
)
from app.permissions import CANDIDATE, EMPLOYEE, STAFF_ROLES
from app.services import storage
from app.services.notification_service import notify, notify_role

# Used when no onboarding template matches the new hire.
DEFAULT_ITEMS = [
    {"title": "Upload government photo ID", "category": "document", "doc_type": "id_proof", "assignee_type": "employee", "due_offset_days": -3, "priority": "high"},
    {"title": "Upload signed offer letter", "category": "document", "doc_type": "signed_offer", "assignee_type": "employee", "due_offset_days": -3, "priority": "high"},
    {"title": "Upload bank account details form", "category": "document", "doc_type": "bank_details", "assignee_type": "employee", "due_offset_days": 0, "priority": "medium"},
    {"title": "Acknowledge code of conduct", "category": "policy", "assignee_type": "employee", "due_offset_days": 1, "priority": "high"},
    {"title": "Acknowledge information security policy", "category": "policy", "assignee_type": "employee", "due_offset_days": 1, "priority": "high"},
    {"title": "Send joining instructions", "category": "pre_boarding", "assignee_type": "hr", "due_offset_days": -5, "priority": "high"},
    {"title": "Laptop and accessories", "category": "it", "assignee_type": "it", "due_offset_days": -1, "priority": "high"},
    {"title": "Email and collaboration accounts", "category": "it", "assignee_type": "it", "due_offset_days": -1, "priority": "high"},
    {"title": "Day-one orientation session", "category": "orientation", "assignee_type": "hr", "due_offset_days": 0, "priority": "medium"},
    {"title": "Meet your manager: role expectations", "category": "manager", "assignee_type": "manager", "due_offset_days": 1, "priority": "medium"},
    {"title": "Complete workplace safety & POSH training", "category": "training", "assignee_type": "employee", "due_offset_days": 14, "priority": "medium"},
]


def _it_type(title: str) -> str:
    t = title.lower()
    if "laptop" in t or "hardware" in t or "equipment" in t:
        return "laptop"
    if "email" in t or "account" in t:
        return "email_account"
    if "access" in t or "system" in t:
        return "system_access"
    return "other"


def select_template(department_id: int, designation: str) -> OnboardingTemplate | None:
    templates = db.session.scalars(sa.select(OnboardingTemplate).where(OnboardingTemplate.is_active.is_(True)))
    best, best_score = None, -1
    for t in templates:
        score = -1
        dept_ok = t.department_id is None or t.department_id == department_id
        role_ok = not t.role_keyword or t.role_keyword.lower() in (designation or "").lower()
        if dept_ok and role_ok:
            score = (2 if t.department_id == department_id else 0) + (2 if t.role_keyword else 0) + (1 if t.is_default else 0)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 0 else None


def apply_template(employee: Employee, template: OnboardingTemplate | None, actor) -> int:
    items = ([{"title": i.title, "description": i.description, "category": i.category, "assignee_type": i.assignee_type,
               "due_offset_days": i.due_offset_days, "priority": i.priority, "doc_type": i.doc_type, "id": i.id}
              for i in template.items] if template else DEFAULT_ITEMS)
    created = 0
    for item in items:
        due = employee.joining_date + timedelta(days=item.get("due_offset_days", 0))
        category = item["category"]
        if category == "document":
            if any(d.doc_type == item["doc_type"] for d in employee.documents):
                continue
            employee.documents.append(EmployeeDocument(doc_type=item["doc_type"], title=item["title"], required=True))
        elif category == "training":
            employee.trainings.append(TrainingAssignment(title=item["title"], description=item.get("description"),
                                                         due_date=due, assigned_by_id=getattr(actor, "id", None)))
        else:
            if category == "it":
                employee.it_requests.append(ITAccessRequest(request_type=_it_type(item["title"]), details=item["title"],
                                                            requested_by_id=getattr(actor, "id", None)))
            assignee_user = {"employee": employee.user_id, "manager": employee.manager_id}.get(item["assignee_type"])
            employee.tasks.append(OnboardingTask(
                title=item["title"], description=item.get("description"), category=category,
                assignee_type=item["assignee_type"], assignee_user_id=assignee_user, due_date=due,
                priority=item.get("priority", "medium"), template_item_id=item.get("id"),
                created_by_id=getattr(actor, "id", None)))
        created += 1
    return created


def schedule_probation(employee: Employee) -> None:
    days = int(get_setting("probation_days") or 0)
    if days <= 0:
        return
    start = employee.joining_date
    employee.probation_end_date = start + timedelta(days=days)
    plan = [("check_in", "30-day manager check-in", 30), ("goal_review", "60-day goals review", 60),
            ("probation_review", "Probation review", days)]
    for kind, title, offset in plan:
        if offset > days:
            continue
        employee.milestones.append(ProbationMilestone(kind=kind, title=title, due_date=start + timedelta(days=offset)))


def create_employee_from_offer(offer, actor) -> Employee:
    """Create exactly one employee for an accepted offer. Safe to call repeatedly."""
    app = offer.application
    user = app.candidate.user
    existing = db.session.scalar(sa.select(Employee).where(sa.or_(
        Employee.offer_id == offer.id, Employee.application_id == app.id, Employee.user_id == user.id)))
    if existing is not None:
        if existing.offer_id == offer.id or existing.application_id == app.id:
            return existing
        raise Conflict("This person already has an employee record.")
    job = app.job
    employee = Employee(
        employee_code=f"TMP-{secrets.token_hex(6)}", user_id=user.id, candidate_id=app.candidate_id,
        application_id=app.id, offer_id=offer.id, department_id=offer.department_id,
        manager_id=job.hiring_manager_id, designation=offer.designation, employment_type=job.employment_type,
        joining_date=offer.joining_date, employment_status="pre_boarding", work_location=job.location,
    )
    db.session.add(employee)
    db.session.flush()  # unique constraints on offer/application/user fire here
    employee.employee_code = f"IN{offer.joining_date.year}{employee.id:05d}"
    if user.role == CANDIDATE:
        user.role = EMPLOYEE
    schedule_probation(employee)
    template = select_template(employee.department_id, employee.designation)
    count = apply_template(employee, template, actor)
    record_audit("employee.create", "employee", employee.id,
                 {"offer_id": offer.id, "application_id": app.id, "template": getattr(template, "name", "default"),
                  "items": count}, actor=actor)
    notify(user, "Welcome aboard!", "Your onboarding checklist is ready.", url_for("portal.onboarding"), "onboarding", email=True)
    notify(employee.manager_id, "New team member", f"{user.full_name} joins as {employee.designation}",
           url_for("employees.profile", employee_id=employee.id), "onboarding")
    notify_role(("hr_manager",), "New employee created", f"{user.full_name} ({employee.employee_code})",
                url_for("employees.profile", employee_id=employee.id), "onboarding")
    return employee


def update_assignment(employee: Employee, data, actor) -> Employee:
    if not access.can_manage_employee(actor, employee):
        raise Forbidden()
    if data.manager_id is not None:
        mgr = db.session.get(User, data.manager_id)
        if mgr is None or not mgr.active or mgr.role not in STAFF_ROLES:
            raise ValidationFailed("Manager must be an active staff user.")
    before = {"department_id": employee.department_id, "manager_id": employee.manager_id}
    for key, value in data.model_dump().items():
        setattr(employee, key, value)
    for task in employee.tasks:
        if task.assignee_type == "manager" and task.status != "completed":
            task.assignee_user_id = employee.manager_id
    record_audit("employee.assignment", "employee", employee.id, {"before": before, "after": data.model_dump(mode="json")})
    return employee


# ---------------------------------------------------------------------- tasks
def can_update_task(user, task: OnboardingTask) -> bool:
    if user.can("onboarding.manage"):
        return True
    if task.assignee_user_id == user.id:
        return True
    if task.assignee_type == "employee" and task.employee.user_id == user.id:
        return True
    return task.assignee_type == "manager" and task.employee.manager_id == user.id


def create_task(employee: Employee, data, actor) -> OnboardingTask:
    manager_ok = actor.can("onboarding.manager_actions") and employee.manager_id == actor.id
    if not (actor.can("onboarding.manage") or manager_ok):
        raise Forbidden("You cannot assign onboarding tasks to this employee.")
    assignee = data.assignee_user_id
    if assignee is None:
        assignee = {"employee": employee.user_id, "manager": employee.manager_id}.get(data.assignee_type)
    elif db.session.get(User, assignee) is None:
        raise ValidationFailed("Assignee not found.")
    task = OnboardingTask(employee_id=employee.id, title=data.title, description=data.description,
                          category=data.category, assignee_type=data.assignee_type, assignee_user_id=assignee,
                          due_date=data.due_date, priority=data.priority, created_by_id=actor.id)
    db.session.add(task)
    db.session.flush()
    record_audit("onboarding.task.create", "onboarding_task", task.id, {"employee_id": employee.id})
    notify(assignee, "Onboarding task assigned", task.title,
           url_for("portal.onboarding") if assignee == employee.user_id else url_for("employees.profile", employee_id=employee.id),
           "onboarding")
    refresh_employment_status(employee)
    return task


def update_task_status(task: OnboardingTask, status: str, blocked_reason: str | None, actor) -> OnboardingTask:
    if not can_update_task(actor, task):
        raise Forbidden("You cannot update this task.")
    if task.status == "completed" and status != "completed" and not actor.can("onboarding.manage"):
        raise InvalidTransition("Only HR can reopen a completed task.")
    task.status = status
    task.blocked_reason = blocked_reason if status == "blocked" else None
    if status == "completed":
        task.completed_at = utcnow()
        task.completed_by_id = actor.id
    else:
        task.completed_at = None
        task.completed_by_id = None
    record_audit("onboarding.task.status", "onboarding_task", task.id, {"status": status})
    if status == "blocked":
        notify_role(("hr_manager",), "Onboarding task blocked", f"{task.title}: {blocked_reason}",
                    url_for("employees.profile", employee_id=task.employee_id), "onboarding")
    refresh_employment_status(task.employee)
    return task


def refresh_employment_status(employee: Employee) -> None:
    tasks = list(employee.tasks)
    if employee.employment_status == "pre_boarding" and (
        employee.joining_date <= today_utc() or any(t.status != "pending" for t in tasks)
    ):
        employee.employment_status = "onboarding"
    if employee.employment_status == "onboarding":
        tasks_done = tasks and all(t.status == "completed" for t in tasks)
        docs_done = all(d.status == "verified" for d in employee.documents if d.required)
        if tasks_done and docs_done:
            employee.employment_status = "probation" if employee.probation_end_date else "active"
            record_audit("employee.status", "employee", employee.id, {"to": employee.employment_status})


def send_overdue_reminders() -> int:
    """Notify assignees of overdue tasks (at most once per 24h per task)."""
    now = utcnow()
    cutoff = now - timedelta(hours=24)
    tasks = db.session.scalars(sa.select(OnboardingTask).where(
        OnboardingTask.status != "completed", OnboardingTask.due_date < today_utc(),
        sa.or_(OnboardingTask.reminder_sent_at.is_(None), OnboardingTask.reminder_sent_at < cutoff)))
    sent = 0
    for task in tasks:
        target = task.assignee_user_id
        link = url_for("employees.profile", employee_id=task.employee_id)
        if task.assignee_type == "employee":
            target = task.employee.user_id
            link = url_for("portal.onboarding")
        if target:
            notify(target, "Overdue onboarding task", f"{task.title} was due {task.due_date:%d %b %Y}.", link, "reminder", email=True)
        else:
            notify_role(("hr_manager",), "Overdue onboarding task",
                        f"{task.title} for {task.employee.user.full_name}", link, "reminder")
        task.reminder_sent_at = now
        sent += 1
    return sent


# ------------------------------------------------------------------ documents
def upload_document(doc: EmployeeDocument, file_storage, actor) -> EmployeeDocument:
    if doc.employee.user_id != actor.id:
        raise Forbidden("Employees upload their own documents.")
    if doc.status == "verified":
        raise InvalidTransition("This document is already verified.")
    stored, _data = storage.save_upload("documents", file_storage, storage.DOCUMENT_EXTENSIONS)
    storage.delete_file("documents", doc.stored_filename)
    doc.stored_filename = stored.stored_filename
    doc.original_filename = stored.original_filename
    doc.content_type = stored.content_type
    doc.size_bytes = stored.size_bytes
    doc.status = "submitted"
    doc.submitted_at = utcnow()
    doc.review_notes = None
    record_audit("document.upload", "employee_document", doc.id, {"doc_type": doc.doc_type})
    notify_role(("hr_manager",), "Document submitted for verification",
                f"{doc.employee.user.full_name}: {doc.title}", url_for("employees.profile", employee_id=doc.employee_id), "document")
    refresh_employment_status(doc.employee)
    return doc


def review_document(doc: EmployeeDocument, decision: str, notes: str | None, actor) -> EmployeeDocument:
    if not actor.can("onboarding.manage"):
        raise Forbidden("Only HR can verify documents.")
    if doc.status != "submitted":
        raise InvalidTransition("Only submitted documents can be reviewed.")
    doc.status = decision
    doc.review_notes = notes
    doc.verified_by_id = actor.id
    doc.verified_at = utcnow()
    record_audit(f"document.{decision}", "employee_document", doc.id, {"notes": notes})
    notify(doc.employee.user_id, f"Document {decision}", f"{doc.title}" + (f": {notes}" if notes else ""),
           url_for("portal.onboarding"), "document")
    refresh_employment_status(doc.employee)
    return doc


def request_document(employee: Employee, doc_type: str, title: str, actor) -> EmployeeDocument:
    if not actor.can("onboarding.manage"):
        raise Forbidden()
    if any(d.doc_type == doc_type for d in employee.documents):
        raise Conflict("This document type is already requested.")
    doc = EmployeeDocument(employee_id=employee.id, doc_type=doc_type, title=title, required=True)
    db.session.add(doc)
    db.session.flush()
    record_audit("document.request", "employee_document", doc.id, {"doc_type": doc_type})
    notify(employee.user_id, "Document requested", title, url_for("portal.onboarding"), "document")
    return doc


# ------------------------------------------------------------------- training
def assign_training(employee: Employee, data, actor) -> TrainingAssignment:
    if not (actor.can("onboarding.manage") or (actor.can("onboarding.manager_actions") and employee.manager_id == actor.id)):
        raise Forbidden()
    payload = data.model_dump()
    payload.pop("employee_id", None)
    t = TrainingAssignment(employee_id=employee.id, assigned_by_id=actor.id, **payload)
    db.session.add(t)
    db.session.flush()
    record_audit("training.assign", "training", t.id, {"employee_id": employee.id})
    notify(employee.user_id, "Training assigned", t.title, url_for("portal.onboarding"), "training")
    return t


def update_training(t: TrainingAssignment, status: str, actor) -> TrainingAssignment:
    if not (t.employee.user_id == actor.id or actor.can("onboarding.manage")):
        raise Forbidden()
    t.status = status
    t.completed_at = utcnow() if status == "completed" else None
    record_audit("training.status", "training", t.id, {"status": status})
    return t


# ----------------------------------------------------------------- IT access
def create_it_request(employee: Employee, data, actor) -> ITAccessRequest:
    if not (employee.user_id == actor.id or actor.can("onboarding.manage") or employee.manager_id == actor.id):
        raise Forbidden()
    req = ITAccessRequest(employee_id=employee.id, request_type=data.request_type, details=data.details,
                          requested_by_id=actor.id)
    db.session.add(req)
    db.session.flush()
    record_audit("it_request.create", "it_request", req.id, {"type": data.request_type})
    notify_role(("hr_manager",), "IT / equipment request", f"{employee.user.full_name}: {data.details}",
                url_for("onboarding.it_requests"), "it")
    return req


IT_TRANSITIONS = {"requested": {"approved", "rejected", "fulfilled"}, "approved": {"fulfilled", "rejected"},
                  "fulfilled": set(), "rejected": {"requested"}}


def update_it_request(req: ITAccessRequest, status: str, notes: str | None, actor) -> ITAccessRequest:
    if not actor.can("onboarding.manage"):
        raise Forbidden("Only HR/IT coordinators can process IT requests.")
    if status not in IT_TRANSITIONS.get(req.status, set()):
        raise InvalidTransition(f"Cannot move a {req.status} request to {status}.")
    req.status = status
    req.notes = notes
    req.handled_by_id = actor.id
    req.handled_at = utcnow()
    record_audit("it_request.status", "it_request", req.id, {"status": status})
    notify(req.employee.user_id, f"IT request {status}", req.details, url_for("portal.onboarding"), "it")
    return req


# ------------------------------------------------------------------ probation
def create_milestone(employee: Employee, data, actor) -> ProbationMilestone:
    if not (actor.can("onboarding.manage") or (actor.can("onboarding.manager_actions") and employee.manager_id == actor.id)):
        raise Forbidden()
    m = ProbationMilestone(employee_id=employee.id, kind=data.kind, title=data.title, due_date=data.due_date, goals=data.goals)
    db.session.add(m)
    db.session.flush()
    record_audit("probation.milestone.create", "milestone", m.id, {"kind": data.kind})
    return m


def complete_milestone(m: ProbationMilestone, data, actor) -> ProbationMilestone:
    employee = m.employee
    if not (actor.can("onboarding.manage") or (actor.can("onboarding.manager_actions") and employee.manager_id == actor.id)):
        raise Forbidden("Only the employee's manager or HR can record check-ins.")
    if m.status == "completed":
        raise InvalidTransition("This milestone is already completed.")
    if m.kind == "probation_review" and not data.outcome:
        raise ValidationFailed("Select a probation outcome.")
    if m.kind != "probation_review" and data.outcome:
        raise ValidationFailed("Outcomes are recorded only on probation reviews.")
    m.status = "completed"
    m.feedback = data.feedback
    m.rating = data.rating
    m.goals = data.goals or m.goals
    m.outcome = data.outcome
    m.completed_by_id = actor.id
    m.completed_at = utcnow()
    if m.kind == "probation_review":
        if data.outcome == "confirmed":
            employee.employment_status = "active"
        elif data.outcome == "extended":
            employee.probation_end_date = (employee.probation_end_date or today_utc()) + timedelta(days=30)
            employee.milestones.append(ProbationMilestone(kind="probation_review", title="Extended probation review",
                                                          due_date=employee.probation_end_date))
    record_audit("probation.milestone.complete", "milestone", m.id, {"kind": m.kind, "outcome": data.outcome})
    notify(employee.user_id, f"{m.title} recorded", "Your manager recorded feedback.", url_for("portal.onboarding"), "probation")
    return m
