"""Audit logging helper. Adds the record to the current transaction (caller commits)."""
from __future__ import annotations

from flask import has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog

_SENSITIVE_KEYS = {"password", "password_hash", "token", "secret", "extracted_text", "resume_text"}


def _clean(details: dict | None) -> dict:
    if not details:
        return {}
    out = {}
    for key, value in details.items():
        if key.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(value, str) and len(value) > 300:
            value = value[:300] + "…"
        out[key] = value
    return out


def record_audit(action: str, entity_type: str, entity_id=None, details: dict | None = None, actor=None) -> AuditLog:
    if actor is None and has_request_context() and current_user and current_user.is_authenticated:
        actor = current_user
    ip = None
    if has_request_context():
        ip = (request.access_route[0] if request.access_route else request.remote_addr) or None
    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=_clean(details),
        ip_address=ip,
    )
    db.session.add(entry)
    return entry
