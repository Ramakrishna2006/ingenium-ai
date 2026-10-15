"""HR onboarding workspace: task board, templates, training, IT requests and probation milestones."""
from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, flash, render_template, request
from flask_login import current_user

from app import access
from app.audit import record_audit
from app.errors import Conflict
from app.extensions import db
from app.models import (
    Department, Employee, ITAccessRequest, OnboardingTask, OnboardingTemplate, OnboardingTemplateItem,
    ProbationMilestone, TrainingAssignment, today_utc,
)
from app.models.employees import (
    ASSIGNEE_TYPES, CATEGORY_LABELS, IT_REQUEST_STATUSES, PRIORITIES, TASK_STATUSES, TRAINING_STATUSES,
)
from app.permissions import permission_required
from app.routes._helpers import done, paginate
from app.schemas import ITRequestUpdateIn, TemplateIn, TemplateItemIn, form_to_dict
from app.services import onboarding_service as obs
from app.services.ai_service import AIError, get_ai

bp = Blueprint("onboarding", __name__, url_prefix="/hr/onboarding")


@bp.get("/tasks")
@permission_required("onboarding.manage")
def tasks():
    status = request.args.get("status")
    assignee = request.args.get("assignee")
    overdue = request.args.get("overdue") == "1"
    stmt = sa.select(OnboardingTask).join(Employee)
    if status in TASK_STATUSES:
        stmt = stmt.where(OnboardingTask.status == status)
    elif not overdue:
        stmt = stmt.where(OnboardingTask.status != "completed")
    if assignee in ASSIGNEE_TYPES:
        stmt = stmt.where(OnboardingTask.assignee_type == assignee)
    if overdue:
        stmt = stmt.where(OnboardingTask.status != "completed", OnboardingTask.due_date < today_utc())
    page = paginate(stmt.order_by(OnboardingTask.due_date.asc().nulls_last()), per_page=25)
    counts = dict(db.session.execute(sa.select(OnboardingTask.status, sa.func.count(OnboardingTask.id))
                                     .group_by(OnboardingTask.status)).all())
    overdue_count = db.session.scalar(sa.select(sa.func.count(OnboardingTask.id)).where(
        OnboardingTask.status != "completed", OnboardingTask.due_date < today_utc()))
    return render_template("onboarding/tasks.html", page=page, counts=counts, overdue_count=overdue_count,
                           filters={"status": status, "assignee": assignee, "overdue": overdue},
                           statuses=TASK_STATUSES, assignee_types=ASSIGNEE_TYPES, today=today_utc())


@bp.post("/tasks/reminders")
@permission_required("onboarding.manage")
def reminders():
    sent = obs.send_overdue_reminders()
    record_audit("onboarding.reminders", "onboarding_task", None, {"sent": sent})
    return done(f"{sent} overdue reminder(s) sent.", "onboarding.tasks", "info")


# ------------------------------------------------------------------ templates
@bp.get("/templates")
@permission_required("onboarding.templates")
def templates():
    items = db.session.scalars(sa.select(OnboardingTemplate).order_by(OnboardingTemplate.name)).all()
    departments = db.session.scalars(sa.select(Department).order_by(Department.name)).all()
    return render_template("onboarding/templates.html", templates=items, departments=departments)


@bp.post("/templates")
@permission_required("onboarding.templates")
def create_template():
    data = TemplateIn.model_validate(form_to_dict(request.form, bool_fields=("is_default", "is_active")))
    if db.session.scalar(sa.select(OnboardingTemplate.id).where(OnboardingTemplate.name == data.name)):
        raise Conflict("A template with this name already exists.")
    t = OnboardingTemplate(**data.model_dump())
    db.session.add(t)
    db.session.flush()
    record_audit("onboarding.template.create", "onboarding_template", t.id, {"name": t.name})
    return done("Template created. Add checklist items next.", "onboarding.template_detail", template_id=t.id)


@bp.get("/templates/<int:template_id>")
@permission_required("onboarding.templates")
def template_detail(template_id):
    t = access.get_or_404(OnboardingTemplate, template_id)
    departments = db.session.scalars(sa.select(Department).order_by(Department.name)).all()
    return render_template("onboarding/template_detail.html", t=t, departments=departments, categories=CATEGORY_LABELS,
                           assignee_types=ASSIGNEE_TYPES, priorities=PRIORITIES)


@bp.post("/templates/<int:template_id>")
@permission_required("onboarding.templates")
def update_template(template_id):
    t = access.get_or_404(OnboardingTemplate, template_id)
    data = TemplateIn.model_validate(form_to_dict(request.form, bool_fields=("is_default", "is_active")))
    for key, value in data.model_dump().items():
        setattr(t, key, value)
    record_audit("onboarding.template.update", "onboarding_template", t.id)
    return done("Template saved.", "onboarding.template_detail", template_id=t.id)


@bp.post("/templates/<int:template_id>/items")
@permission_required("onboarding.templates")
def add_item(template_id):
    t = access.get_or_404(OnboardingTemplate, template_id)
    data = TemplateItemIn.model_validate(form_to_dict(request.form))
    t.items.append(OnboardingTemplateItem(**data.model_dump(), sort_order=len(t.items) + 1))
    record_audit("onboarding.template.item_add", "onboarding_template", t.id, {"title": data.title})
    return done("Checklist item added.", "onboarding.template_detail", template_id=t.id)


@bp.post("/templates/items/<int:item_id>/delete")
@permission_required("onboarding.templates")
def delete_item(item_id):
    item = access.get_or_404(OnboardingTemplateItem, item_id)
    template_id = item.template_id
    db.session.delete(item)
    record_audit("onboarding.template.item_delete", "onboarding_template", template_id, {"item": item_id})
    return done("Item removed.", "onboarding.template_detail", template_id=template_id)


@bp.post("/templates/<int:template_id>/ai-suggest")
@permission_required("onboarding.templates")
def ai_suggest(template_id):
    t = access.get_or_404(OnboardingTemplate, template_id)
    try:
        suggestion = get_ai().onboarding_suggestions(t.role_keyword or t.name, t.department.name if t.department else "General")
    except AIError as exc:
        flash(f"AI suggestions unavailable: {exc.message}", "warning")
        return done("You can still add items manually.", "onboarding.template_detail", "secondary", template_id=t.id)
    existing = {i.title.lower() for i in t.items}
    added = 0
    for item in suggestion.items:
        if item.title.lower() in existing:
            continue
        doc_type = None
        category = item.category
        if category == "document":
            doc_type = "_".join(item.title.lower().split()[:3])[:60]
            doc_type = "".join(ch for ch in doc_type if ch.isalnum() or ch == "_") or "document"
        t.items.append(OnboardingTemplateItem(title=item.title, category=category, assignee_type=item.assignee_type,
                                              due_offset_days=item.due_offset_days, doc_type=doc_type,
                                              sort_order=len(t.items) + 1))
        added += 1
    record_audit("onboarding.template.ai_suggest", "onboarding_template", t.id, {"added": added})
    return done(f"{added} AI-suggested items added — review and remove any that don't fit.", "onboarding.template_detail",
                template_id=t.id)


# ------------------------------------------------------------------- training
@bp.get("/training")
@permission_required("onboarding.manage")
def training():
    status = request.args.get("status")
    stmt = sa.select(TrainingAssignment)
    if status in TRAINING_STATUSES:
        stmt = stmt.where(TrainingAssignment.status == status)
    page = paginate(stmt.order_by(TrainingAssignment.due_date.asc().nulls_last()), per_page=25)
    counts = dict(db.session.execute(sa.select(TrainingAssignment.status, sa.func.count(TrainingAssignment.id))
                                     .group_by(TrainingAssignment.status)).all())
    return render_template("onboarding/training.html", page=page, counts=counts, status=status,
                           statuses=TRAINING_STATUSES, today=today_utc())


# ---------------------------------------------------------------- IT requests
@bp.get("/it-requests")
@permission_required("onboarding.manage")
def it_requests():
    status = request.args.get("status")
    stmt = sa.select(ITAccessRequest)
    if status in IT_REQUEST_STATUSES:
        stmt = stmt.where(ITAccessRequest.status == status)
    page = paginate(stmt.order_by(ITAccessRequest.created_at.desc()), per_page=25)
    return render_template("onboarding/it_requests.html", page=page, status=status, statuses=IT_REQUEST_STATUSES,
                           transitions=obs.IT_TRANSITIONS)


@bp.post("/it-requests/<int:req_id>")
@permission_required("onboarding.manage")
def update_it_request(req_id):
    req = access.get_or_404(ITAccessRequest, req_id)
    data = ITRequestUpdateIn.model_validate(form_to_dict(request.form))
    obs.update_it_request(req, data.status, data.notes, current_user)
    return done(f"Request marked {data.status}.", "onboarding.it_requests")


# ------------------------------------------------------------------- probation
@bp.get("/probation")
@permission_required("onboarding.manager_actions")
def probation():
    show = request.args.get("show", "open")
    stmt = sa.select(ProbationMilestone).join(Employee)
    if not access.is_org_wide(current_user):
        stmt = stmt.where(Employee.manager_id == current_user.id)
    if show == "open":
        stmt = stmt.where(ProbationMilestone.status == "scheduled")
    page = paginate(stmt.order_by(ProbationMilestone.due_date), per_page=25)
    return render_template("onboarding/probation.html", page=page, show=show, today=today_utc())
