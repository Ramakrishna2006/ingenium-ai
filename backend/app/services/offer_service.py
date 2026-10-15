"""Offer lifecycle, approvals, PDF letters and transactional acceptance -> employee creation."""
from __future__ import annotations

import io
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app, url_for
from sqlalchemy.exc import IntegrityError

from app import access
from app.audit import record_audit
from app.errors import Conflict, Forbidden, InvalidTransition, ValidationFailed
from app.extensions import db
from app.models import Department, Offer, OfferApproval, get_setting, today_utc, utcnow
from app.models.hiring import OFFER_ACTIVE_STATUSES, OFFER_TRANSITIONS
from app.services import storage
from app.services.notification_service import notify, notify_role
from app.services.onboarding_service import create_employee_from_offer
from app.services.recruitment_service import transition_application


def _check_transition(offer: Offer, to_status: str):
    if to_status not in OFFER_TRANSITIONS.get(offer.status, set()):
        raise InvalidTransition(f"An offer that is '{offer.status_label}' cannot move to '{to_status.replace('_', ' ')}'.")


def _validate_terms(data):
    if db.session.get(Department, data.department_id) is None:
        raise ValidationFailed("Department not found.")
    if data.joining_date <= today_utc():
        raise ValidationFailed("Joining date must be in the future.")
    if data.expires_on and data.expires_on < today_utc():
        raise ValidationFailed("Offer expiry cannot be in the past.")
    if data.expires_on and data.expires_on > data.joining_date:
        raise ValidationFailed("Offer must expire before the joining date.")


def create_offer(application, data, actor) -> Offer:
    if not actor.can("offer.create"):
        raise Forbidden("Only HR can create offers.")
    if application.status != "approved_for_offer":
        raise InvalidTransition("Offers can only be created for applications approved for an offer.")
    active = db.session.scalar(sa.select(Offer).where(Offer.application_id == application.id,
                                                      Offer.status.in_(OFFER_ACTIVE_STATUSES)))
    if active:
        raise Conflict("This application already has an active offer.")
    _validate_terms(data)
    values = data.model_dump()
    if not values.get("expires_on"):
        values["expires_on"] = min(today_utc() + timedelta(days=int(get_setting("offer_validity_days") or 7)),
                                   data.joining_date - timedelta(days=1))
    offer = Offer(application_id=application.id, created_by_id=actor.id, status="draft", **values)
    db.session.add(offer)
    try:
        with db.session.begin_nested():
            db.session.flush()
    except IntegrityError as exc:
        raise Conflict("This application already has an active offer.") from exc
    record_audit("offer.create", "offer", offer.id, {"application_id": application.id})
    return offer


def update_offer(offer: Offer, data, actor) -> Offer:
    if not actor.can("offer.create"):
        raise Forbidden()
    if offer.status not in ("draft", "revision_requested"):
        raise InvalidTransition("Only draft offers or offers returned for revision can be edited.")
    _validate_terms(data)
    for key, value in data.model_dump().items():
        if value is not None or key in ("terms",):
            setattr(offer, key, value)
    offer.version += 1
    if offer.status == "revision_requested":
        offer.status = "draft"
    record_audit("offer.update", "offer", offer.id, {"version": offer.version})
    return offer


def submit_offer(offer: Offer, actor) -> Offer:
    if not actor.can("offer.create"):
        raise Forbidden()
    _check_transition(offer, "pending_approval")
    offer.status = "pending_approval"
    record_audit("offer.submit", "offer", offer.id)
    notify_role(("hr_manager", "super_admin"), "Offer awaiting approval",
                f"{offer.application.candidate.user.full_name} – {offer.designation}",
                url_for("offers.detail", offer_id=offer.id), "offer")
    return offer


def decide_offer(offer: Offer, decision: str, comments: str | None, actor) -> Offer:
    if not actor.can("offer.approve"):
        raise Forbidden("You are not an offer approver.")
    if current_app.config.get("OFFER_REQUIRE_SEPARATE_APPROVER") and offer.created_by_id == actor.id:
        raise Forbidden("Offers must be approved by someone other than their author.")
    _check_transition(offer, decision)
    if decision == "approved" and offer.expires_on and offer.expires_on < today_utc():
        raise InvalidTransition("The offer expiry date has passed. Revise the offer first.")
    db.session.add(OfferApproval(offer_id=offer.id, approver_id=actor.id, decision=decision,
                                 comments=comments, offer_version=offer.version))
    offer.status = decision
    if decision == "approved":
        offer.extended_at = utcnow()
        transition_application(offer.application, "offered", actor, "Offer extended", system=True)
        generate_offer_pdf(offer)
    else:
        notify(offer.created_by_id, "Offer revision requested", comments or "", url_for("offers.detail", offer_id=offer.id), "offer")
    record_audit(f"offer.{decision}", "offer", offer.id, {"comments": comments, "version": offer.version})
    return offer


def withdraw_offer(offer: Offer, reason: str, actor) -> Offer:
    if not actor.can("offer.approve"):
        raise Forbidden()
    _check_transition(offer, "withdrawn")
    was_extended = offer.status == "approved"
    offer.status = "withdrawn"
    offer.decline_reason = reason
    db.session.add(OfferApproval(offer_id=offer.id, approver_id=actor.id, decision="withdrawn", comments=reason,
                                 offer_version=offer.version))
    if was_extended:
        transition_application(offer.application, "approved_for_offer", actor, "Offer withdrawn", internal=True, system=True)
    record_audit("offer.withdraw", "offer", offer.id, {"reason": reason})
    return offer


def respond_to_offer(offer_id: int, response: str, reason: str | None, actor):
    """Candidate accepts/declines. Acceptance + employee creation is one transaction.

    The offer row is locked (SELECT ... FOR UPDATE on PostgreSQL) so concurrent or
    repeated requests cannot create two employees; repeated accepts are idempotent.
    """
    offer = db.session.scalar(sa.select(Offer).where(Offer.id == offer_id).with_for_update())
    if offer is None or not access.is_application_owner(actor, offer.application):
        raise Forbidden("You can only respond to your own offers.")
    if response == "accept" and offer.status == "accepted":
        employee = create_employee_from_offer(offer, actor)  # returns the existing record
        return offer, employee
    if offer.status != "approved":
        raise InvalidTransition("This offer is not open for a response.")
    if offer.expires_on and offer.expires_on < today_utc():
        offer.status = "expired"
        record_audit("offer.expired", "offer", offer.id)
        db.session.commit()
        raise InvalidTransition("This offer has expired. Please contact HR.")
    offer.responded_at = utcnow()
    employee = None
    if response == "accept":
        offer.status = "accepted"
        transition_application(offer.application, "hired", actor, "Offer accepted", system=True)
        employee = create_employee_from_offer(offer, actor)
        notify_role(("hr_manager",), "Offer accepted", f"{actor.full_name} accepted {offer.designation}",
                    url_for("offers.detail", offer_id=offer.id), "offer")
    else:
        offer.status = "declined"
        offer.decline_reason = reason
        transition_application(offer.application, "offer_declined", actor, reason or "Offer declined", system=True)
        notify_role(("hr_manager",), "Offer declined", f"{actor.full_name} declined {offer.designation}",
                    url_for("offers.detail", offer_id=offer.id), "offer")
    record_audit(f"offer.{offer.status}", "offer", offer.id, {"reason": reason})
    return offer, employee


def expire_offers() -> int:
    offers = db.session.scalars(sa.select(Offer).where(Offer.status == "approved", Offer.expires_on < today_utc()))
    count = 0
    for offer in offers:
        offer.status = "expired"
        record_audit("offer.expired", "offer", offer.id, actor=None)
        notify(offer.application.candidate.user_id, "Offer expired", offer.designation, None, "offer")
        count += 1
    return count


def _footer(canvas, doc):
    """Small 'Prepared with Ingenium AI' mark (the Spark logo) at the foot of each offer-letter page."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    size = 5 * mm
    x, y = doc.leftMargin, 9 * mm
    k = size / 40.0  # logo is drawn on a 40x40 grid
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#4f46e5"))
    canvas.roundRect(x, y, size, size, 11 * k, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    star = canvas.beginPath()
    pts = [(20, 4.8), (21.6, 9.3), (26, 10.9), (21.6, 12.5), (20, 17), (18.4, 12.5), (14, 10.9), (18.4, 9.3)]
    for i, (px, py) in enumerate(pts):
        (star.moveTo if i == 0 else star.lineTo)(x + px * k, y + size - py * k)
    star.close()
    canvas.drawPath(star, stroke=0, fill=1)
    canvas.roundRect(x + 17.6 * k, y + size - 33.5 * k, 4.8 * k, 14 * k, 2.4 * k, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(x + size + 2 * mm, y + 1.6 * mm, "Prepared with Ingenium AI")
    canvas.restoreState()


def generate_offer_pdf(offer: Offer) -> str:
    """Render the offer letter to a private PDF file and store its name on the offer."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    app = offer.application
    org = get_setting("org_name") or "Ingenium AI"
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=f"Offer letter - {offer.designation}")
    styles = getSampleStyleSheet()
    brand = ParagraphStyle("brand", parent=styles["Title"], textColor=colors.HexColor("#4338ca"), alignment=0)
    body = ParagraphStyle("body", parent=styles["BodyText"], leading=15)
    story = [
        Paragraph(escape(org), brand),
        Paragraph(f"Date: {offer.extended_at:%d %B %Y}" if offer.extended_at else "", body),
        Spacer(1, 8 * mm),
        Paragraph(f"Dear {escape(app.candidate.user.full_name)},", body),
        Spacer(1, 4 * mm),
        Paragraph(f"We are pleased to offer you the position of <b>{escape(offer.designation)}</b> in the "
                  f"<b>{escape(offer.department.name)}</b> department, subject to the terms below.", body),
        Spacer(1, 5 * mm),
    ]
    rows = [["Designation", offer.designation], ["Department", offer.department.name],
            ["Annual compensation", f"{offer.currency} {offer.salary:,.2f}"],
            ["Employment type", app.job.employment_type_label],
            ["Joining date", f"{offer.joining_date:%d %B %Y}"],
            ["Offer valid until", f"{offer.expires_on:%d %B %Y}" if offer.expires_on else "—"],
            ["Reference", f"OFFER-{offer.id}-v{offer.version}"]]
    table = Table(rows, colWidths=[55 * mm, 110 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2ff")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d2fe")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table, Spacer(1, 6 * mm)]
    if offer.terms:
        story.append(Paragraph("<b>Terms and conditions</b>", body))
        for para in offer.terms.splitlines():
            if para.strip():
                story.append(Paragraph(escape(para), body))
        story.append(Spacer(1, 4 * mm))
    story += [
        Paragraph("Please accept or decline this offer through the Ingenium AI candidate portal before the "
                  "validity date.", body),
        Spacer(1, 10 * mm),
        Paragraph(f"For {escape(org)} — Human Resources", body),
    ]
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    old = offer.pdf_stored_filename
    stored = storage.save_bytes("offers", buffer.getvalue(), "pdf", f"offer-{offer.id}.pdf")
    offer.pdf_stored_filename = stored.stored_filename
    storage.delete_file("offers", old)
    return stored.stored_filename
