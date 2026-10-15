"""In-app notifications with optional e-mail delivery (only when MAIL_SERVER is configured)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import sqlalchemy as sa
from flask import current_app

from app.extensions import db
from app.models import Notification, User, utcnow

log = logging.getLogger(__name__)


def mail_configured() -> bool:
    return bool(current_app.config.get("MAIL_SERVER"))


def send_email(to: str, subject: str, body: str) -> bool:
    """Best-effort email; never raises into the request. Returns True if sent."""
    if not mail_configured():
        return False
    cfg = current_app.config
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=10) as smtp:
            if cfg.get("MAIL_USE_TLS"):
                smtp.starttls()
            if cfg.get("MAIL_USERNAME"):
                smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Email delivery failed: %s", type(exc).__name__)
        return False


def notify(user: User | int | None, title: str, body: str = "", link: str | None = None,
           category: str = "general", email: bool = False) -> Notification | None:
    """Create an in-app notification (added to the current transaction)."""
    if user is None:
        return None
    user_obj = user if isinstance(user, User) else db.session.get(User, user)
    if user_obj is None:
        return None
    note = Notification(user_id=user_obj.id, title=title[:200], body=body, link=link, category=category)
    db.session.add(note)
    if email:
        send_email(user_obj.email, f"Ingenium AI: {title}", f"{body}\n\n{link or ''}")
    return note


def notify_role(roles: tuple[str, ...], title: str, body: str = "", link: str | None = None, category: str = "general"):
    users = db.session.scalars(sa.select(User).where(User.role.in_(roles), User.active.is_(True)))
    for user in users:
        notify(user, title, body, link, category)


def unread_count(user_id: int) -> int:
    return db.session.scalar(
        sa.select(sa.func.count(Notification.id)).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    ) or 0


def mark_read(user_id: int, notification_id: int | None = None) -> int:
    stmt = sa.update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    result = db.session.execute(stmt.values(read_at=utcnow()))
    return result.rowcount or 0
