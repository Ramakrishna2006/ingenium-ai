from __future__ import annotations

import sqlalchemy as sa
from flask import Blueprint, render_template, request
from flask_login import current_user

from app import access
from app.errors import Forbidden, ValidationFailed
from app.extensions import db
from app.models import Application, Department, Offer, get_setting
from app.models.hiring import OFFER_STATUS_LABELS
from app.permissions import permission_required
from app.routes._helpers import done, paginate
from app.schemas import OfferDecisionIn, OfferIn, form_to_dict
from app.services import offer_service

bp = Blueprint("offers", __name__, url_prefix="/hr/offers")


def _visible(offer_id) -> Offer:
    offer = access.get_or_404(Offer, offer_id, "Offer not found.")
    if not current_user.is_staff or not access.can_view_offer(current_user, offer):
        raise Forbidden("You do not have access to this offer.")
    return offer


def _departments():
    return db.session.scalars(sa.select(Department).where(Department.is_active.is_(True)).order_by(Department.name)).all()


@bp.get("/")
@permission_required("offer.view")
def list_offers():
    status = request.args.get("status")
    stmt = access.offer_scope(current_user)
    if status in OFFER_STATUS_LABELS:
        stmt = stmt.where(Offer.status == status)
    page = paginate(stmt.order_by(Offer.updated_at.desc()))
    ready = []
    if current_user.can("offer.create"):
        ready = db.session.scalars(sa.select(Application).where(Application.status == "approved_for_offer")).all()
        ready = [a for a in ready if not any(o.status in ("draft", "pending_approval", "revision_requested", "approved")
                                             for o in a.offers)]
    return render_template("offers/list.html", page=page, status=status, labels=OFFER_STATUS_LABELS, ready=ready)


@bp.route("/new/<int:app_id>", methods=["GET", "POST"])
@permission_required("offer.create")
def new(app_id):
    app = access.get_or_404(Application, app_id)
    if request.method == "POST":
        offer = offer_service.create_offer(app, OfferIn.model_validate(form_to_dict(request.form)), current_user)
        if request.form.get("submit_for_approval") == "1":
            offer_service.submit_offer(offer, current_user)
        return done("Offer saved.", "offers.detail", offer_id=offer.id)
    if app.status != "approved_for_offer":
        raise ValidationFailed("This application has not been approved for an offer.")
    return render_template("offers/form.html", app=app, offer=None, departments=_departments(),
                           currency=get_setting("default_currency"))


@bp.route("/<int:offer_id>/edit", methods=["GET", "POST"])
@permission_required("offer.create")
def edit(offer_id):
    offer = _visible(offer_id)
    if request.method == "POST":
        offer_service.update_offer(offer, OfferIn.model_validate(form_to_dict(request.form)), current_user)
        return done("Offer updated.", "offers.detail", offer_id=offer.id)
    return render_template("offers/form.html", app=offer.application, offer=offer, departments=_departments(),
                           currency=offer.currency)


@bp.get("/<int:offer_id>")
@permission_required("offer.view")
def detail(offer_id):
    offer = _visible(offer_id)
    return render_template("offers/detail.html", offer=offer)


@bp.post("/<int:offer_id>/submit")
@permission_required("offer.create")
def submit(offer_id):
    offer_service.submit_offer(_visible(offer_id), current_user)
    return done("Offer submitted for approval.", "offers.detail", offer_id=offer_id)


@bp.post("/<int:offer_id>/decision")
@permission_required("offer.approve")
def decision(offer_id):
    offer = _visible(offer_id)
    data = OfferDecisionIn.model_validate(form_to_dict(request.form))
    offer_service.decide_offer(offer, data.decision, data.comments, current_user)
    msg = "Offer approved and extended to the candidate." if data.decision == "approved" else "Revision requested."
    return done(msg, "offers.detail", offer_id=offer.id)


@bp.post("/<int:offer_id>/withdraw")
@permission_required("offer.approve")
def withdraw(offer_id):
    reason = (request.form.get("reason") or "").strip()
    if len(reason) < 5:
        raise ValidationFailed("Provide a reason for withdrawing the offer.")
    offer_service.withdraw_offer(_visible(offer_id), reason[:1000], current_user)
    return done("Offer withdrawn.", "offers.detail", "warning", offer_id=offer_id)
