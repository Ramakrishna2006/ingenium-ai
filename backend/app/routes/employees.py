"""Employee directory and profile (assignment, documents, tasks, training, IT, probation)."""
from __future__ import annotations

import re

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app import access
from app.errors import Forbidden, ValidationFailed
from app.extensions import db
from app.models import (
    Department, Employee, EmployeeDocument, OnboardingTask, ProbationMilestone,
    TrainingAssignment, User, today_utc,
)
from app.models.employees import (
    ASSIGNEE_TYPES, CATEGORY_LABELS, EMPLOYMENT_STATUS_LABELS, IT_REQUEST_TYPES, MILESTONE_KINDS, PRIORITIES,
    PROBATION_OUTCOMES, TASK_STATUSES,
)
from app.permissions import STAFF_ROLES, any_permission_required, permission_required
from app.routes._helpers import arg_int, done, paginate
from app.schemas import (
    DocumentReviewIn, EmployeeAssignIn, ITRequestIn, MilestoneCompleteIn, MilestoneIn, TaskIn, TaskStatusIn,
    TrainingIn, TrainingStatusIn, form_to_dict,
)
from app.services import onboarding_service as obs

bp = Blueprint("employees", __name__, url_prefix="/hr/employees")


def _visible(employee_id) -> Employee:
    emp = access.get_or_404(Employee, employee_id, "Employee not found.")
    if not access.can_view_employee(current_user, emp) or not current_user.is_staff:
        raise Forbidden("You do not have access to this employee record.")
    return emp


@bp.get("/")
@permission_required("employee.view")
def directory():
    q = (request.args.get("q") or "").strip()[:80]
    dept = arg_int("department")
    status = request.args.get("status")
    stmt = access.employee_scope(current_user).join(User, User.id == Employee.user_id)
    if q:
        stmt = stmt.where(sa.or_(User.full_name.ilike(f"%{q}%"), Employee.employee_code.ilike(f"%{q}%"),
                                 Employee.designation.ilike(f"%{q}%")))
    if dept:
        stmt = stmt.where(Employee.department_id == dept)
    if status in EMPLOYMENT_STATUS_LABELS:
        stmt = stmt.where(Employee.employment_status == status)
    page = paginate(stmt.order_by(Employee.joining_date.desc()))
    departments = db.session.scalars(sa.select(Department).order_by(Department.name)).all()
    return render_template("employees/directory.html", page=page, departments=departments,
                           statuses=EMPLOYMENT_STATUS_LABELS, filters={"q": q, "department": dept, "status": status})


@bp.get("/<int:employee_id>")
@permission_required("employee.view")
def profile(employee_id):
    emp = _visible(employee_id)
    staff = db.session.scalars(sa.select(User).where(User.role.in_(STAFF_ROLES), User.active.is_(True)).order_by(User.full_name)).all()
    departments = db.session.scalars(sa.select(Department).where(Department.is_active.is_(True)).order_by(Department.name)).all()
    return render_template(
        "employees/profile.html", emp=emp, staff=staff, departments=departments,
        can_manage=access.can_manage_employee(current_user, emp),
        is_manager=emp.manager_id == current_user.id, hr=current_user.can("onboarding.manage"),
        categories=CATEGORY_LABELS, assignee_types=ASSIGNEE_TYPES, priorities=PRIORITIES, task_statuses=TASK_STATUSES,
        it_types=IT_REQUEST_TYPES, milestone_kinds=MILESTONE_KINDS, outcomes=PROBATION_OUTCOMES,
        can_update_task=lambda t: obs.can_update_task(current_user, t), today=today_utc(),
    )


@bp.post("/<int:employee_id>/assignment")
@permission_required("employee.manage")
def assignment(employee_id):
    emp = _visible(employee_id)
    obs.update_assignment(emp, EmployeeAssignIn.model_validate(form_to_dict(request.form)), current_user)
    return done("Assignment updated.", "employees.profile", employee_id=emp.id)


@bp.post("/<int:employee_id>/tasks")
@any_permission_required("onboarding.manage", "onboarding.manager_actions")
def add_task(employee_id):
    emp = _visible(employee_id)
    data = TaskIn.model_validate({**form_to_dict(request.form), "employee_id": emp.id})
    obs.create_task(emp, data, current_user)
    return done("Task assigned.", "employees.profile", employee_id=emp.id)


@bp.post("/tasks/<int:task_id>/status")
@login_required
def task_status(task_id):
    task = access.get_or_404(OnboardingTask, task_id)
    data = TaskStatusIn.model_validate(form_to_dict(request.form))
    obs.update_task_status(task, data.status, data.blocked_reason, current_user)
    endpoint = "portal.onboarding" if task.employee.user_id == current_user.id and not current_user.is_staff else None
    if endpoint:
        return done("Task updated.", endpoint)
    return done("Task updated.", "employees.profile", employee_id=task.employee_id)


@bp.post("/documents/<int:doc_id>/review")
@permission_required("onboarding.manage")
def review_document(doc_id):
    doc = access.get_or_404(EmployeeDocument, doc_id)
    data = DocumentReviewIn.model_validate(form_to_dict(request.form))
    obs.review_document(doc, data.decision, data.notes, current_user)
    return done(f"Document {data.decision}.", "employees.profile", employee_id=doc.employee_id)


@bp.post("/<int:employee_id>/documents")
@permission_required("onboarding.manage")
def request_document(employee_id):
    emp = _visible(employee_id)
    title = (request.form.get("title") or "").strip()[:150]
    doc_type = re.sub(r"[^a-z0-9_]", "_", (request.form.get("doc_type") or title).strip().lower())[:60]
    if len(title) < 2 or not doc_type.strip("_"):
        raise ValidationFailed("Enter a document title.")
    obs.request_document(emp, doc_type, title, current_user)
    return done("Document requested from the employee.", "employees.profile", employee_id=emp.id)


@bp.post("/<int:employee_id>/training")
@any_permission_required("onboarding.manage", "onboarding.manager_actions")
def add_training(employee_id):
    emp = _visible(employee_id)
    data = TrainingIn.model_validate({**form_to_dict(request.form, bool_fields=("is_mandatory",)), "employee_id": emp.id})
    obs.assign_training(emp, data, current_user)
    return done("Training assigned.", "employees.profile", employee_id=emp.id)


@bp.post("/training/<int:training_id>/status")
@login_required
def training_status(training_id):
    t = access.get_or_404(TrainingAssignment, training_id)
    obs.update_training(t, TrainingStatusIn.model_validate(form_to_dict(request.form)).status, current_user)
    if t.employee.user_id == current_user.id and not current_user.is_staff:
        return done("Training updated.", "portal.onboarding")
    return done("Training updated.", "employees.profile", employee_id=t.employee_id)


@bp.post("/<int:employee_id>/it-requests")
@login_required
def add_it_request(employee_id):
    emp = access.get_or_404(Employee, employee_id)
    data = ITRequestIn.model_validate({**form_to_dict(request.form), "employee_id": emp.id})
    obs.create_it_request(emp, data, current_user)
    if emp.user_id == current_user.id and not current_user.is_staff:
        return done("Request sent to HR/IT.", "portal.onboarding")
    return done("IT request created.", "employees.profile", employee_id=emp.id)


@bp.post("/<int:employee_id>/milestones")
@any_permission_required("onboarding.manage", "onboarding.manager_actions")
def add_milestone(employee_id):
    emp = _visible(employee_id)
    obs.create_milestone(emp, MilestoneIn.model_validate({**form_to_dict(request.form), "employee_id": emp.id}), current_user)
    return done("Check-in scheduled.", "employees.profile", employee_id=emp.id)


@bp.post("/milestones/<int:milestone_id>/complete")
@any_permission_required("onboarding.manage", "onboarding.manager_actions")
def complete_milestone(milestone_id):
    m = access.get_or_404(ProbationMilestone, milestone_id)
    _visible(m.employee_id)
    obs.complete_milestone(m, MilestoneCompleteIn.model_validate(form_to_dict(request.form)), current_user)
    return done("Check-in recorded.", "employees.profile", employee_id=m.employee_id)

