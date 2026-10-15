from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, utcnow


class Department(TimestampMixin, db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    code = db.Column(db.String(20), nullable=False, unique=True)
    description = db.Column(db.Text)
    location = db.Column(db.String(120))
    head_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    head = db.relationship("User", foreign_keys=[head_user_id])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Department {self.code}>"


class OrgSetting(db.Model):
    """Key/value organisation settings editable by Super Admins."""

    __tablename__ = "org_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))


DEFAULT_SETTINGS = {
    "org_name": "Ingenium Demo Organisation",
    "default_timezone": "Asia/Kolkata",
    "default_currency": "INR",
    "probation_days": 90,
    "offer_validity_days": 7,
    "resume_retention_days": 365,
    "ollama_model_override": "",
    "allow_application_withdrawal": True,
}


def get_setting(key: str):
    row = db.session.get(OrgSetting, key)
    if row is None or row.value is None:
        return DEFAULT_SETTINGS.get(key)
    return row.value


def set_setting(key: str, value, user_id: int | None = None) -> None:
    row = db.session.get(OrgSetting, key)
    if row is None:
        row = OrgSetting(key=key)
        db.session.add(row)
    row.value = value
    row.updated_by_id = user_id
