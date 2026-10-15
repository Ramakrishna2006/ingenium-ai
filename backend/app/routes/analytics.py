from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from flask import Blueprint, render_template, request

from app.extensions import db
from app.models import Department
from app.permissions import permission_required
from app.services.ai_service import AIError, get_ai
from app.services.analytics_service import compute_metrics, default_range

bp = Blueprint("analytics", __name__, url_prefix="/hr/analytics")


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _range():
    start, end = _parse_date(request.values.get("start")), _parse_date(request.values.get("end"))
    if not start or not end:
        days = request.values.get("days", 90, type=int)
        start, end = default_range(max(7, min(days, 730)))
    return start, end, request.values.get("department", type=int)


@bp.route("/", methods=["GET", "POST"])
@permission_required("analytics.view")
def index():
    start, end, dept = _range()
    metrics = compute_metrics(start, end, dept)
    ai_summary, ai_error = None, None
    if request.method == "POST":
        try:
            compact = {k: metrics[k] for k in ("range", "funnel", "offers", "time_to_hire", "onboarding", "open_vacancies")}
            ai_summary = get_ai().summarize_report(compact).model_dump()
        except AIError as exc:
            ai_error = exc.message
    departments = db.session.scalars(sa.select(Department).order_by(Department.name)).all()
    return render_template("analytics/index.html", m=metrics, departments=departments, dept=dept,
                           start=start, end=end, ai_summary=ai_summary, ai_error=ai_error)
