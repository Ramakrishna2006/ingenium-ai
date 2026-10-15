"""Domain exceptions and centralised error handling (JSON for /api, HTML otherwise)."""
from __future__ import annotations

import logging

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError
from pydantic import ValidationError as PydanticValidationError
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    default_message = "The request could not be processed."

    def __init__(self, message: str | None = None, *, code: str | None = None, details=None,
                 status_code: int | None = None):
        message = message or self.default_message
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"
    default_message = "Some of the information provided is not valid."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    default_message = "The requested item was not found."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission for this action."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    default_message = "This request conflicts with the current state of the record."


class InvalidTransition(Conflict):
    code = "invalid_transition"
    default_message = "This action is not allowed in the record's current status."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    default_message = "The service is temporarily unavailable."


def is_api_request() -> bool:
    return request.path.startswith("/api/")


def error_payload(code: str, message: str, details=None, status: int = 400):
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return jsonify(body), status


def pydantic_details(exc: PydanticValidationError):
    return [
        {"field": ".".join(str(p) for p in err.get("loc", ())), "message": err.get("msg", "invalid")}
        for err in exc.errors(include_url=False, include_context=False, include_input=False)
    ]


def register_error_handlers(app):
    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        from app.extensions import db

        db.session.rollback()
        if is_api_request():
            return error_payload(exc.code, exc.message, exc.details, exc.status_code)
        if request.method == "GET" and exc.status_code in (403, 404):
            return render_template("errors/error.html", status=exc.status_code, message=exc.message), exc.status_code
        flash(exc.message, "danger")
        return redirect(_safe_back())

    @app.errorhandler(PydanticValidationError)
    def handle_pydantic(exc: PydanticValidationError):
        from app.extensions import db

        db.session.rollback()
        details = pydantic_details(exc)
        if is_api_request():
            return error_payload("validation_error", "Request validation failed.", details, 422)
        flash("Please correct the form: " + "; ".join(f"{d['field']}: {d['message']}" for d in details), "danger")
        return redirect(_safe_back())

    @app.errorhandler(CSRFError)
    def handle_csrf(exc: CSRFError):
        if is_api_request():
            return error_payload("csrf_failed", "Missing or invalid CSRF token.", status=400)
        flash("Your session form token expired. Please try again.", "warning")
        return redirect(_safe_back())

    @app.errorhandler(HTTPException)
    def handle_http(exc: HTTPException):
        status = exc.code or 500
        if is_api_request():
            code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                    413: "payload_too_large", 429: "rate_limited"}.get(status, "http_error")
            message = {401: "Authentication required.", 403: "You do not have permission for this action.",
                       413: "Uploaded file is too large."}.get(status, exc.description or exc.name)
            return error_payload(code, message, status=status)
        if status == 401:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else None))
        if status == 413:
            flash("The uploaded file is too large.", "danger")
            return redirect(_safe_back())
        return render_template("errors/error.html", status=status, message=_friendly(status)), status

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):  # pragma: no cover - safety net
        log.exception("Unhandled error on %s %s", request.method, request.path)
        from app.extensions import db

        db.session.rollback()
        if is_api_request():
            return error_payload("internal_error", "An unexpected error occurred.", status=500)
        return render_template("errors/error.html", status=500, message=_friendly(500)), 500


def _friendly(status: int) -> str:
    return {
        403: "You do not have permission to view this page.",
        404: "We couldn't find that page.",
        405: "That action isn't allowed here.",
        429: "Too many requests. Please wait a moment and try again.",
        500: "Something went wrong on our side. The error has been logged.",
    }.get(status, "An error occurred.")


def _safe_back() -> str:
    """Return the referrer only if it is same-origin, else the dashboard."""
    ref = request.referrer
    if ref and ref.startswith(request.host_url):
        return ref
    return url_for("main.dashboard")
