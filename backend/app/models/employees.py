"""Employees, documents, onboarding, training, IT access and probation."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, status_check
from app.models.recruitment import EMPLOYMENT_TYPES

EMPLOYMENT_STATUSES = ("pre_boarding", "onboarding", "probation", "active", "exited")
EMPLOYMENT_STATUS_LABELS = {
    "pre_boarding": "Pre-boarding", "onboarding": "Onboarding", "probation": "Probation",
    "active": "Active", "exited": "Exited",
}


class Employee(TimestampMixin, db.Model):
    __tablename__ = "employees"
    __table_args__ = (
        status_check("employment_status", EMPLOYMENT_STATUSES, "employment_status_valid"),
        status_check("employment_type", EMPLOYMENT_TYPES, "employment_type_valid"),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_code = db.Column(db.String(20), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, unique=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="SET NULL"))
    # Unique: one employee per application/offer -> idempotent hiring.
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="SET NULL"), unique=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offers.id", ondelete="SET NULL"), unique=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True)
    manager_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    designation = db.Column(db.String(150), nullable=False)
    employment_type = db.Column(db.String(20), nullable=False, default="full_time")
    joining_date = db.Column(db.Date, nullable=False)
    probation_end_date = db.Column(db.Date)
    employment_status = db.Column(db.String(20), nullable=False, default="pre_boarding", index=True)
    work_location = db.Column(db.String(120))

    user = db.relationship("User", back_populates="employee", foreign_keys=[user_id])
    manager = db.relationship("User", foreign_keys=[manager_id])
    department = db.relationship("Department")
    candidate = db.relationship("Candidate")
    offer = db.relationship("Offer")
    documents = db.relationship("EmployeeDocument", back_populates="employee", cascade="all, delete-orphan",
                                order_by="EmployeeDocument.id")
    tasks = db.relationship("OnboardingTask", back_populates="employee", cascade="all, delete-orphan",
                            order_by="OnboardingTask.due_date")
    trainings = db.relationship("TrainingAssignment", back_populates="employee", cascade="all, delete-orphan",
                                order_by="TrainingAssignment.due_date")
    it_requests = db.relationship("ITAccessRequest", back_populates="employee", cascade="all, delete-orphan",
                                  order_by="ITAccessRequest.id")
    milestones = db.relationship("ProbationMilestone", back_populates="employee", cascade="all, delete-orphan",
                                 order_by="ProbationMilestone.due_date")

    @property
    def status_label(self) -> str:
        return EMPLOYMENT_STATUS_LABELS.get(self.employment_status, self.employment_status)

    @property
    def onboarding_progress(self) -> int:
        tasks = list(self.tasks)
        if not tasks:
            return 0
        return round(100 * sum(1 for t in tasks if t.status == "completed") / len(tasks))


DOCUMENT_STATUSES = ("requested", "submitted", "verified", "rejected")


class EmployeeDocument(TimestampMixin, db.Model):
    __tablename__ = "employee_documents"
    __table_args__ = (
        db.UniqueConstraint("employee_id", "doc_type", name="uq_employee_document_type"),
        status_check("status", DOCUMENT_STATUSES),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    doc_type = db.Column(db.String(60), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    required = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(20), nullable=False, default="requested", index=True)
    stored_filename = db.Column(db.String(80), unique=True)
    original_filename = db.Column(db.String(255))
    content_type = db.Column(db.String(100))
    size_bytes = db.Column(db.Integer)
    submitted_at = db.Column(db.DateTime(timezone=True))
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    verified_at = db.Column(db.DateTime(timezone=True))
    review_notes = db.Column(db.String(500))

    employee = db.relationship("Employee", back_populates="documents")
    verified_by = db.relationship("User")


TEMPLATE_ITEM_CATEGORIES = ("pre_boarding", "orientation", "policy", "document", "it", "training", "hr", "manager")
CATEGORY_LABELS = {
    "pre_boarding": "Pre-boarding", "orientation": "Orientation", "policy": "Policy acknowledgement",
    "document": "Document", "it": "IT & equipment", "training": "Training", "hr": "HR", "manager": "Manager",
}
ASSIGNEE_TYPES = ("employee", "hr", "it", "manager")
PRIORITIES = ("low", "medium", "high")


class OnboardingTemplate(TimestampMixin, db.Model):
    __tablename__ = "onboarding_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id", ondelete="SET NULL"), index=True)
    role_keyword = db.Column(db.String(80))  # matched against designation (case-insensitive)
    description = db.Column(db.Text)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    department = db.relationship("Department")
    items = db.relationship("OnboardingTemplateItem", back_populates="template", cascade="all, delete-orphan",
                            order_by="OnboardingTemplateItem.sort_order")


class OnboardingTemplateItem(db.Model):
    __tablename__ = "onboarding_template_items"
    __table_args__ = (
        status_check("category", TEMPLATE_ITEM_CATEGORIES, "category_valid"),
        status_check("assignee_type", ASSIGNEE_TYPES, "assignee_type_valid"),
        status_check("priority", PRIORITIES, "priority_valid"),
    )

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("onboarding_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(20), nullable=False, default="orientation")
    assignee_type = db.Column(db.String(20), nullable=False, default="employee")
    due_offset_days = db.Column(db.Integer, nullable=False, default=0)  # relative to joining date
    priority = db.Column(db.String(10), nullable=False, default="medium")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    doc_type = db.Column(db.String(60))  # for category == "document"

    template = db.relationship("OnboardingTemplate", back_populates="items")


TASK_STATUSES = ("pending", "in_progress", "completed", "blocked")


class OnboardingTask(TimestampMixin, db.Model):
    __tablename__ = "onboarding_tasks"
    __table_args__ = (
        status_check("status", TASK_STATUSES),
        status_check("assignee_type", ASSIGNEE_TYPES, "assignee_type_valid"),
        status_check("priority", PRIORITIES, "priority_valid"),
        status_check("category", TEMPLATE_ITEM_CATEGORIES, "category_valid"),
        db.Index("ix_onboarding_tasks_status_due", "status", "due_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    template_item_id = db.Column(db.Integer, db.ForeignKey("onboarding_template_items.id", ondelete="SET NULL"))
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(20), nullable=False, default="orientation")
    assignee_type = db.Column(db.String(20), nullable=False, default="employee")
    assignee_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    due_date = db.Column(db.Date)
    priority = db.Column(db.String(10), nullable=False, default="medium")
    status = db.Column(db.String(20), nullable=False, default="pending")
    blocked_reason = db.Column(db.String(255))
    completed_at = db.Column(db.DateTime(timezone=True))
    completed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reminder_sent_at = db.Column(db.DateTime(timezone=True))

    employee = db.relationship("Employee", back_populates="tasks")
    assignee = db.relationship("User", foreign_keys=[assignee_user_id])
    completed_by = db.relationship("User", foreign_keys=[completed_by_id])

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


TRAINING_STATUSES = ("assigned", "in_progress", "completed")


class TrainingAssignment(TimestampMixin, db.Model):
    __tablename__ = "training_assignments"
    __table_args__ = (status_check("status", TRAINING_STATUSES),)

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    resource_url = db.Column(db.String(500))
    due_date = db.Column(db.Date)
    is_mandatory = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(20), nullable=False, default="assigned", index=True)
    completed_at = db.Column(db.DateTime(timezone=True))
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    employee = db.relationship("Employee", back_populates="trainings")


IT_REQUEST_TYPES = ("laptop", "email_account", "system_access", "equipment", "other")
IT_REQUEST_STATUSES = ("requested", "approved", "fulfilled", "rejected")


class ITAccessRequest(TimestampMixin, db.Model):
    __tablename__ = "it_access_requests"
    __table_args__ = (
        status_check("status", IT_REQUEST_STATUSES),
        status_check("request_type", IT_REQUEST_TYPES, "request_type_valid"),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    request_type = db.Column(db.String(30), nullable=False)
    details = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="requested", index=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    handled_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    handled_at = db.Column(db.DateTime(timezone=True))
    notes = db.Column(db.String(500))

    employee = db.relationship("Employee", back_populates="it_requests")


MILESTONE_KINDS = ("check_in", "goal_review", "probation_review")
MILESTONE_STATUSES = ("scheduled", "completed", "missed")
PROBATION_OUTCOMES = ("confirmed", "extended", "not_confirmed")


class ProbationMilestone(TimestampMixin, db.Model):
    __tablename__ = "probation_milestones"
    __table_args__ = (
        status_check("status", MILESTONE_STATUSES),
        status_check("kind", MILESTONE_KINDS, "kind_valid"),
        db.CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 5", name="rating_range"),
        db.CheckConstraint(
            "outcome IS NULL OR outcome IN ('confirmed', 'extended', 'not_confirmed')", name="outcome_valid"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="scheduled")
    goals = db.Column(db.Text)
    feedback = db.Column(db.Text)
    rating = db.Column(db.Integer)
    outcome = db.Column(db.String(20))
    completed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    completed_at = db.Column(db.DateTime(timezone=True))

    employee = db.relationship("Employee", back_populates="milestones")
    completed_by = db.relationship("User")
