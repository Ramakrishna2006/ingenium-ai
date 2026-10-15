"""Shared model helpers."""
from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa

from app.extensions import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """Return an aware UTC datetime (SQLite returns naive values)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def today_utc() -> date:
    return utcnow().date()


def status_check(column: str, values: tuple[str, ...], name: str | None = None) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name or f"{column}_valid")


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
