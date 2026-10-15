from __future__ import annotations

from functools import wraps

from flask import abort, current_app, flash, redirect, request, url_for
from flask_login import current_user

from app.extensions import db


def paginate(stmt, per_page: int | None = None):
    page = request.args.get("page", 1, type=int)
    return db.paginate(stmt, page=max(page, 1), per_page=per_page or current_app.config["ITEMS_PER_PAGE"],
                       error_out=False)


def done(message: str, endpoint: str, category: str = "success", **values):
    db.session.commit()
    flash(message, category)
    return redirect(url_for(endpoint, **values))


def candidate_required(view):
    """The current user must be able to use the candidate portal (candidate or employee)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.can("self.candidate"):
            abort(403)
        if current_user.candidate is None:
            from app.services.recruitment_service import ensure_candidate_profile

            ensure_candidate_profile(current_user)
            db.session.commit()
        return view(*args, **kwargs)

    return wrapped


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not current_user.is_staff:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def arg_int(name: str):
    value = request.args.get(name, type=int)
    return value if value and value > 0 else None
