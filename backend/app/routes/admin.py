"""Administration: users & roles, departments, AI service, audit log and system settings."""
from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import current_user

from app import access
from app.audit import record_audit
from app.errors import Conflict, ValidationFailed
from app.extensions import db
from app.models import AuditLog, Department, User, get_setting, set_setting
from app.models.organization import DEFAULT_SETTINGS
from app.permissions import ROLE_LABELS, ROLES, SUPER_ADMIN, permission_required
from app.routes._helpers import done, paginate
from app.schemas import DepartmentIn, ModelConfigIn, SettingsIn, UserCreateIn, UserUpdateIn, form_to_dict
from app.security import validate_password
from app.services import recruitment_service as rs
from app.services.ai_service import get_ai

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------- users
@bp.get("/users")
@permission_required("admin.users")
def users():
    q = (request.args.get("q") or "").strip()[:80]
    role = request.args.get("role")
    stmt = sa.select(User)
    if q:
        stmt = stmt.where(sa.or_(User.full_name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    if role in ROLES:
        stmt = stmt.where(User.role == role)
    page = paginate(stmt.order_by(User.created_at.desc()), per_page=20)
    return render_template("admin/users.html", page=page, roles=ROLE_LABELS, q=q, role=role)


@bp.post("/users")
@permission_required("admin.users")
def create_user():
    data = UserCreateIn.model_validate(form_to_dict(request.form))
    validate_password(data.password, data.email)
    if db.session.scalar(sa.select(User.id).where(User.email == data.email)):
        raise Conflict("A user with this email already exists.")
    user = User(email=data.email, full_name=data.full_name, role=data.role)
    user.set_password(data.password)
    db.session.add(user)
    db.session.flush()
    if data.role == "candidate":
        rs.ensure_candidate_profile(user)
    record_audit("user.create", "user", user.id, {"role": data.role})
    return done(f"User {user.email} created. Share the temporary password securely and ask them to change it.", "admin.users")


def _super_admin_count() -> int:
    return db.session.scalar(sa.select(sa.func.count(User.id)).where(User.role == SUPER_ADMIN, User.active.is_(True))) or 0


@bp.post("/users/<int:user_id>")
@permission_required("admin.users")
def update_user(user_id):
    user = access.get_or_404(User, user_id)
    data = UserUpdateIn.model_validate(form_to_dict(request.form, bool_fields=("active",)))
    if user.id == current_user.id and (data.role not in (None, SUPER_ADMIN) or data.active is False):
        raise ValidationFailed("You cannot remove your own administrator access.")
    losing_admin = user.role == SUPER_ADMIN and (data.role not in (None, SUPER_ADMIN) or data.active is False)
    if losing_admin and _super_admin_count() <= 1:
        raise ValidationFailed("At least one active Super Admin is required.")
    if data.role == "employee" and user.employee is None:
        raise ValidationFailed("The employee role is assigned automatically when an offer is accepted.")
    before = {"role": user.role, "active": user.active}
    if data.role:
        user.role = data.role
    if data.active is not None and data.active != user.active:
        user.active = data.active
        user.session_version += 1  # sign out everywhere
    if data.full_name:
        user.full_name = data.full_name
    if before["role"] != user.role:
        user.session_version += 1
    if user.role == "candidate":
        rs.ensure_candidate_profile(user)
    record_audit("user.update", "user", user.id, {"before": before, "after": {"role": user.role, "active": user.active}})
    return done("User updated.", "admin.users")


# ---------------------------------------------------------------- departments
@bp.get("/departments")
@permission_required("department.manage")
def departments():
    items = db.session.scalars(sa.select(Department).order_by(Department.name)).all()
    staff = db.session.scalars(sa.select(User).where(User.role.in_(("super_admin", "hr_manager", "hiring_manager")),
                                                     User.active.is_(True)).order_by(User.full_name)).all()
    edit = db.session.get(Department, request.args.get("edit", type=int) or 0)
    return render_template("admin/departments.html", departments=items, staff=staff, edit=edit)


@bp.post("/departments")
@permission_required("department.manage")
def create_department():
    data = DepartmentIn.model_validate(form_to_dict(request.form, bool_fields=("is_active",)))
    rs.create_department(data, current_user)
    return done(f"Department {data.name} created.", "admin.departments")


@bp.post("/departments/<int:dept_id>")
@permission_required("department.manage")
def update_department(dept_id):
    dept = access.get_or_404(Department, dept_id)
    data = DepartmentIn.model_validate(form_to_dict(request.form, bool_fields=("is_active",)))
    rs.update_department(dept, data, current_user)
    return done("Department updated.", "admin.departments")


# ----------------------------------------------------------------- AI service
@bp.get("/ai")
@permission_required("admin.ai")
def ai_status():
    status = get_ai().status()
    return render_template("admin/ai.html", status=status, override=get_setting("ollama_model_override") or "",
                           can_configure=current_user.can("admin.settings"))


@bp.post("/ai/model")
@permission_required("admin.settings")
def ai_model():
    data = ModelConfigIn.model_validate(form_to_dict(request.form))
    set_setting("ollama_model_override", data.ollama_model_override, current_user.id)
    record_audit("settings.ai_model", "setting", "ollama_model_override", {"value": data.ollama_model_override or "(default)"})
    return done("AI model setting saved.", "admin.ai_status")


# ------------------------------------------------------------------ audit log
@bp.get("/audit")
@permission_required("admin.audit")
def audit():
    action = (request.args.get("action") or "").strip()[:80]
    entity = (request.args.get("entity") or "").strip()[:60]
    actor = request.args.get("actor", type=int)
    stmt = sa.select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"{action}%"))
    if entity:
        stmt = stmt.where(AuditLog.entity_type == entity)
    if actor:
        stmt = stmt.where(AuditLog.actor_id == actor)
    page = paginate(stmt.order_by(AuditLog.created_at.desc()), per_page=40)
    entities = db.session.scalars(sa.select(AuditLog.entity_type).distinct().order_by(AuditLog.entity_type)).all()
    return render_template("admin/audit.html", page=page, filters={"action": action, "entity": entity, "actor": actor},
                           entities=entities)


# ------------------------------------------------------------------- settings
@bp.route("/settings", methods=["GET", "POST"])
@permission_required("admin.settings")
def settings():
    if request.method == "POST":
        data = SettingsIn.model_validate(form_to_dict(request.form, bool_fields=("allow_application_withdrawal",)))
        for key, value in data.model_dump().items():
            set_setting(key, value, current_user.id)
        record_audit("settings.update", "setting", "org", data.model_dump())
        return done("Settings saved.", "admin.settings")
    values = {k: get_setting(k) for k in DEFAULT_SETTINGS}
    return render_template("admin/settings.html", values=values)
