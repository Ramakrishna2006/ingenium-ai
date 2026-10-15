from __future__ import annotations

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db, login_manager
from app.models.base import TimestampMixin, status_check
from app.permissions import ROLE_LABELS, ROLES, STAFF_ROLES, role_has


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (status_check("role", ROLES, "role_valid"),)

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="candidate", index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    email_verified_at = db.Column(db.DateTime(timezone=True))
    last_login_at = db.Column(db.DateTime(timezone=True))
    # Bumped on password change / deactivation to invalidate existing sessions.
    session_version = db.Column(db.Integer, nullable=False, default=1)

    candidate = db.relationship("Candidate", back_populates="user", uselist=False)
    employee = db.relationship(
        "Employee", back_populates="user", uselist=False, foreign_keys="Employee.user_id"
    )

    # --- auth helpers -------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return bool(self.password_hash) and check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # used by Flask-Login
        return bool(self.active)

    def get_id(self) -> str:
        return f"{self.id}:{self.session_version}"

    # --- authorization helpers ---------------------------------------
    def can(self, permission: str) -> bool:
        return self.is_active and role_has(self.role, permission)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def initials(self) -> str:
        parts = [p for p in (self.full_name or "").split() if p]
        return ("".join(p[0] for p in parts[:2]) or "?").upper()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.role}>"


@login_manager.user_loader
def load_user(user_key: str):
    try:
        user_id, version = user_key.split(":", 1)
        user = db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None
    if user is None or str(user.session_version) != version or not user.active:
        return None
    return user
