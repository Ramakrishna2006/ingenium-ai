"""Employee self-service portal — only the signed-in employee's own records."""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from app import access
from app.errors import Forbidden
from app.models import EmployeeDocument, today_utc
from app.models.employees import IT_REQUEST_TYPES
from app.routes._helpers import done
from app.services import onboarding_service as obs

bp = Blueprint("portal", __name__, url_prefix="/portal")


def employee_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        emp = access.current_employee(current_user)
        if emp is None:
            abort(404)
        return view(emp, *args, **kwargs)

    return wrapped


@bp.get("/onboarding")
@employee_required
def onboarding(emp):
    my_tasks = [t for t in emp.tasks if t.assignee_type == "employee" or t.assignee_user_id == current_user.id]
    others = [t for t in emp.tasks if t not in my_tasks]
    return render_template("portal/onboarding.html", emp=emp, my_tasks=my_tasks, others=others, today=today_utc(),
                           it_types=IT_REQUEST_TYPES)


@bp.get("/profile")
@employee_required
def profile(emp):
    return render_template("portal/profile.html", emp=emp)


@bp.post("/documents/<int:doc_id>")
@employee_required
def upload_document(emp, doc_id):
    doc = access.get_or_404(EmployeeDocument, doc_id)
    if doc.employee_id != emp.id:
        raise Forbidden()
    obs.upload_document(doc, request.files.get("file"), current_user)
    return done("Document uploaded for verification.", "portal.onboarding")
