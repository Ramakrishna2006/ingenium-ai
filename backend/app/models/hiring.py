"""Interviews, hiring decisions and offers."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, status_check, utcnow

INTERVIEW_KINDS = ("technical", "behavioral", "role_specific", "hr", "panel")
INTERVIEW_KIND_LABELS = {
    "technical": "Technical", "behavioral": "Behavioural", "role_specific": "Role-specific",
    "hr": "HR", "panel": "Panel",
}
INTERVIEW_STATUSES = ("scheduled", "completed", "cancelled")


class Interview(TimestampMixin, db.Model):
    __tablename__ = "interviews"
    __table_args__ = (
        status_check("status", INTERVIEW_STATUSES),
        status_check("kind", INTERVIEW_KINDS, "kind_valid"),
        db.CheckConstraint("duration_minutes BETWEEN 10 AND 480", name="duration_range"),
    )

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="technical")
    scheduled_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)  # stored in UTC
    timezone = db.Column(db.String(64), nullable=False, default="UTC")  # IANA zone shown to participants
    duration_minutes = db.Column(db.Integer, nullable=False, default=45)
    location = db.Column(db.String(255))
    meeting_link = db.Column(db.String(500))
    status = db.Column(db.String(20), nullable=False, default="scheduled", index=True)
    reschedule_count = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)
    question_bank = db.Column(db.JSON, nullable=False, default=list)
    cancelled_reason = db.Column(db.String(255))
    completed_at = db.Column(db.DateTime(timezone=True))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    application = db.relationship("Application", back_populates="interviews")
    assignments = db.relationship("InterviewerAssignment", back_populates="interview", cascade="all, delete-orphan")
    feedback = db.relationship("InterviewFeedback", back_populates="interview", cascade="all, delete-orphan")
    created_by = db.relationship("User")

    @property
    def kind_label(self) -> str:
        return INTERVIEW_KIND_LABELS.get(self.kind, self.kind)

    @property
    def interviewer_ids(self) -> set[int]:
        return {a.interviewer_id for a in self.assignments}


class InterviewerAssignment(db.Model):
    __tablename__ = "interviewer_assignments"
    __table_args__ = (db.UniqueConstraint("interview_id", "interviewer_id", name="uq_interviewer_assignment"),)

    id = db.Column(db.Integer, primary_key=True)
    interview_id = db.Column(db.Integer, db.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    interviewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    interview = db.relationship("Interview", back_populates="assignments")
    interviewer = db.relationship("User")


RECOMMENDATIONS = ("strong_hire", "hire", "no_hire", "strong_no_hire")
RECOMMENDATION_LABELS = {
    "strong_hire": "Strong hire", "hire": "Hire", "no_hire": "No hire", "strong_no_hire": "Strong no hire",
}
RATING_CRITERIA = ("technical", "problem_solving", "communication", "role_knowledge")


class InterviewFeedback(TimestampMixin, db.Model):
    __tablename__ = "interview_feedback"
    __table_args__ = (
        db.UniqueConstraint("interview_id", "interviewer_id", name="uq_feedback_interview_interviewer"),
        status_check("recommendation", RECOMMENDATIONS, "recommendation_valid"),
        db.CheckConstraint("overall_rating BETWEEN 1 AND 5", name="overall_rating_range"),
    )

    id = db.Column(db.Integer, primary_key=True)
    interview_id = db.Column(db.Integer, db.ForeignKey("interviews.id", ondelete="CASCADE"), nullable=False, index=True)
    interviewer_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    ratings = db.Column(db.JSON, nullable=False, default=dict)
    overall_rating = db.Column(db.Integer, nullable=False)
    recommendation = db.Column(db.String(20), nullable=False)
    strengths = db.Column(db.Text)
    concerns = db.Column(db.Text)
    notes = db.Column(db.Text)

    interview = db.relationship("Interview", back_populates="feedback")
    interviewer = db.relationship("User")

    @property
    def recommendation_label(self) -> str:
        return RECOMMENDATION_LABELS.get(self.recommendation, self.recommendation)


DECISIONS = ("recommend_hire", "recommend_reject", "approve", "reject")
DECISION_LABELS = {
    "recommend_hire": "Recommends hire", "recommend_reject": "Recommends not progressing",
    "approve": "Approved for offer", "reject": "Not progressed",
}


class HiringDecision(db.Model):
    """Append-only decision history. Only humans create these records."""

    __tablename__ = "hiring_decisions"
    __table_args__ = (status_check("decision", DECISIONS, "decision_valid"),)

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    decision = db.Column(db.String(30), nullable=False)
    rationale = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    application = db.relationship("Application", back_populates="decisions")
    decided_by = db.relationship("User")

    @property
    def decision_label(self) -> str:
        return DECISION_LABELS.get(self.decision, self.decision)


OFFER_STATUSES = (
    "draft", "pending_approval", "revision_requested", "approved", "accepted", "declined", "expired", "withdrawn",
)
OFFER_ACTIVE_STATUSES = ("draft", "pending_approval", "revision_requested", "approved", "accepted")
OFFER_TRANSITIONS = {
    "draft": {"pending_approval", "withdrawn"},
    "pending_approval": {"approved", "revision_requested", "withdrawn"},
    "revision_requested": {"draft", "pending_approval", "withdrawn"},
    "approved": {"accepted", "declined", "expired", "withdrawn"},
    "accepted": set(), "declined": set(), "expired": set(), "withdrawn": set(),
}
OFFER_STATUS_LABELS = {
    "draft": "Draft", "pending_approval": "Pending approval", "revision_requested": "Revision requested",
    "approved": "Extended to candidate", "accepted": "Accepted", "declined": "Declined",
    "expired": "Expired", "withdrawn": "Withdrawn",
}

_active = ", ".join(f"'{s}'" for s in OFFER_ACTIVE_STATUSES)


class Offer(TimestampMixin, db.Model):
    __tablename__ = "offers"
    __table_args__ = (
        status_check("status", OFFER_STATUSES),
        db.CheckConstraint("salary > 0", name="salary_positive"),
        # At most one live offer per application (prevents inconsistent offer state).
        db.Index(
            "uq_offers_one_active_per_application", "application_id", unique=True,
            postgresql_where=db.text(f"status IN ({_active})"),
            sqlite_where=db.text(f"status IN ({_active})"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False, index=True)
    designation = db.Column(db.String(150), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False)
    salary = db.Column(db.Numeric(14, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    joining_date = db.Column(db.Date, nullable=False)
    expires_on = db.Column(db.Date)
    terms = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="draft", index=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    pdf_stored_filename = db.Column(db.String(80))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    extended_at = db.Column(db.DateTime(timezone=True))
    responded_at = db.Column(db.DateTime(timezone=True))
    decline_reason = db.Column(db.Text)

    application = db.relationship("Application", back_populates="offers")
    department = db.relationship("Department")
    created_by = db.relationship("User")
    approvals = db.relationship("OfferApproval", back_populates="offer", cascade="all, delete-orphan",
                                order_by="OfferApproval.created_at")

    @property
    def status_label(self) -> str:
        return OFFER_STATUS_LABELS.get(self.status, self.status)


OFFER_APPROVAL_DECISIONS = ("approved", "revision_requested", "withdrawn")


class OfferApproval(db.Model):
    __tablename__ = "offer_approvals"
    __table_args__ = (status_check("decision", OFFER_APPROVAL_DECISIONS, "decision_valid"),)

    id = db.Column(db.Integer, primary_key=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True)
    approver_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    decision = db.Column(db.String(30), nullable=False)
    comments = db.Column(db.Text)
    offer_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    offer = db.relationship("Offer", back_populates="approvals")
    approver = db.relationship("User")
