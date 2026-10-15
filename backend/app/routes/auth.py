from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import ValidationError
from werkzeug.security import check_password_hash, generate_password_hash

from app.audit import record_audit
from app.errors import Conflict, pydantic_details
from app.extensions import db
from app.models import Candidate, User, utcnow
from app.schemas import LoginIn, RegisterIn, form_to_dict
from app.security import is_safe_redirect, password_problems, rate_limit, validate_password
from app.services.notification_service import mail_configured, send_email

bp = Blueprint("auth", __name__, url_prefix="/auth")
_DUMMY_HASH = generate_password_hash("timing-equaliser-not-a-real-password")


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_token(user: User, purpose: str) -> str:
    return _serializer(purpose).dumps({"uid": user.id, "h": user.password_hash[-16:]})


def read_token(token: str, purpose: str, max_age: int) -> User | None:
    try:
        data = _serializer(purpose).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or user.password_hash[-16:] != data.get("h"):
        return None  # token already used (password changed) or user removed
    return user


def register_candidate(data: RegisterIn) -> User:
    """Public registration ALWAYS creates a candidate — the role is never taken from input."""
    validate_password(data.password, data.email)
    if db.session.scalar(sa.select(User.id).where(User.email == data.email)):
        raise Conflict("An account with this email already exists.", code="email_taken")
    user = User(email=data.email, full_name=data.full_name, role="candidate")
    user.set_password(data.password)
    db.session.add(user)
    db.session.flush()
    db.session.add(Candidate(user_id=user.id, skills=[], education=[], certifications=[]))
    record_audit("auth.register", "user", user.id, {"role": "candidate"}, actor=user)
    return user


def authenticate(email: str, password: str) -> User | None:
    user = db.session.scalar(sa.select(User).where(User.email == (email or "").strip().lower()))
    if user is None:
        # Equalise timing with a dummy hash check to avoid user enumeration.
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    if not user.active or not user.check_password(password):
        return None
    return user


@bp.route("/login", methods=["GET", "POST"])
@rate_limit(config_key="LOGIN_RATE_LIMIT")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        try:
            data = LoginIn.model_validate(form_to_dict(request.form, bool_fields=("remember",)))
        except ValidationError:
            flash("Enter your email and password.", "danger")
            return render_template("auth/login.html"), 400
        user = authenticate(data.email, data.password)
        if user is None:
            record_audit("auth.login_failed", "user", None, {"email_domain": data.email.split("@")[-1][:60]}, actor=None)
            db.session.commit()
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", email=data.email), 401
        session.clear()
        session.permanent = True
        login_user(user, remember=data.remember)
        user.last_login_at = utcnow()
        record_audit("auth.login", "user", user.id, actor=user)
        db.session.commit()
        target = request.args.get("next")
        return redirect(target if is_safe_redirect(target) else url_for("main.dashboard"))
    return render_template("auth/login.html")


@bp.route("/register", methods=["GET", "POST"])
@rate_limit("5/600")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        form = form_to_dict(request.form, bool_fields=("consent",))
        try:
            data = RegisterIn.model_validate(form)
        except ValidationError as exc:
            flash("; ".join(d["message"].removeprefix("Value error, ") for d in pydantic_details(exc)), "danger")
            return render_template("auth/register.html", form=form), 422
        user = register_candidate(data)
        db.session.commit()
        if mail_configured():
            link = url_for("auth.verify_email", token=make_token(user, "verify"), _external=True)
            send_email(user.email, "Verify your Ingenium AI email", f"Confirm your email address: {link}")
        session.clear()
        session.permanent = True
        login_user(user)
        flash("Welcome! Your candidate account is ready. Start by uploading your resume.", "success")
        return redirect(url_for("candidate.resumes"))
    return render_template("auth/register.html", form={})


@bp.post("/logout")
@login_required
def logout():
    record_audit("auth.logout", "user", current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/forgot", methods=["GET", "POST"])
@rate_limit("5/600")
def forgot_password():
    enabled = mail_configured()
    if request.method == "POST" and enabled:
        email = (request.form.get("email") or "").strip().lower()
        user = db.session.scalar(sa.select(User).where(User.email == email, User.active.is_(True)))
        if user:
            link = url_for("auth.reset_password", token=make_token(user, "reset"), _external=True)
            send_email(user.email, "Reset your Ingenium AI password",
                       f"Use this link within 1 hour to reset your password:\n{link}\n\nIf you didn't ask for this, ignore this email.")
            record_audit("auth.reset_requested", "user", user.id, actor=None)
            db.session.commit()
        flash("If that email is registered, a reset link has been sent.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html", enabled=enabled)


@bp.route("/reset/<token>", methods=["GET", "POST"])
@rate_limit("10/600")
def reset_password(token):
    user = read_token(token, "reset", max_age=3600)
    if user is None:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != request.form.get("confirm_password"):
            flash("Passwords do not match.", "danger")
        elif problems := password_problems(password, user.email):
            flash("Password must have " + ", ".join(problems) + ".", "danger")
        else:
            user.set_password(password)
            user.session_version += 1
            record_audit("auth.password_reset", "user", user.id, actor=user)
            db.session.commit()
            flash("Password updated. Please sign in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")


@bp.get("/verify/<token>")
def verify_email(token):
    user = read_token(token, "verify", max_age=7 * 86400)
    if user is None:
        flash("This verification link is invalid or has expired.", "danger")
    else:
        user.email_verified_at = user.email_verified_at or utcnow()
        db.session.commit()
        flash("Email verified. Thank you!", "success")
    return redirect(url_for("main.dashboard" if current_user.is_authenticated else "auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
@rate_limit("10/600")
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("password", "")
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
        elif new != request.form.get("confirm_password"):
            flash("New passwords do not match.", "danger")
        elif problems := password_problems(new, current_user.email):
            flash("Password must have " + ", ".join(problems) + ".", "danger")
        else:
            current_user.set_password(new)
            current_user.session_version += 1
            record_audit("auth.password_change", "user", current_user.id)
            db.session.commit()
            login_user(current_user)  # re-issue session with the new version
            flash("Password changed.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/password.html")
