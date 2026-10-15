from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin, status_check, utcnow

EMPLOYMENT_TYPES = ("full_time", "part_time", "internship", "contract")
EMPLOYMENT_TYPE_LABELS = {
    "full_time": "Full-time", "part_time": "Part-time", "internship": "Internship", "contract": "Contract",
}

# ---------------------------------------------------------------------------
# Hiring requests (workforce planning)
# ---------------------------------------------------------------------------
HIRING_REQUEST_STATUSES = ("pending", "approved", "rejected", "revision_requested", "closed")
HIRING_REQUEST_TRANSITIONS = {
    "pending": {"approved", "rejected", "revision_requested"},
    "revision_requested": {"pending", "closed"},
    "approved": {"closed"},
    "rejected": set(),
    "closed": set(),
}


class HiringRequest(TimestampMixin, db.Model):
    __tablename__ = "hiring_requests"
    __table_args__ = (
        status_check("status", HIRING_REQUEST_STATUSES),
        status_check("employment_type", EMPLOYMENT_TYPES, "employment_type_valid"),
        db.CheckConstraint("headcount > 0", name="headcount_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True)
    hiring_manager_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    headcount = db.Column(db.Integer, nullable=False, default=1)
    employment_type = db.Column(db.String(20), nullable=False, default="full_time")
    justification = db.Column(db.Text, nullable=False)
    target_date = db.Column(db.Date)
    budget_amount = db.Column(db.Numeric(14, 2))
    budget_currency = db.Column(db.String(3))
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    decision_notes = db.Column(db.Text)
    decided_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    decided_at = db.Column(db.DateTime(timezone=True))

    department = db.relationship("Department")
    hiring_manager = db.relationship("User", foreign_keys=[hiring_manager_id])
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    decided_by = db.relationship("User", foreign_keys=[decided_by_id])
    jobs = db.relationship("Job", back_populates="hiring_request")


# ---------------------------------------------------------------------------
# Jobs / vacancies
# ---------------------------------------------------------------------------
JOB_STATUSES = ("draft", "published", "paused", "closed", "archived")
JOB_TRANSITIONS = {
    "draft": {"published", "archived"},
    "published": {"paused", "closed"},
    "paused": {"published", "closed"},
    "closed": {"archived", "published"},
    "archived": set(),
}


class Job(TimestampMixin, db.Model):
    __tablename__ = "jobs"
    __table_args__ = (
        status_check("status", JOB_STATUSES),
        status_check("employment_type", EMPLOYMENT_TYPES, "employment_type_valid"),
        db.CheckConstraint("headcount > 0", name="headcount_positive"),
        db.CheckConstraint(
            "experience_max_years IS NULL OR experience_min_years IS NULL OR experience_max_years >= experience_min_years",
            name="experience_range",
        ),
        db.Index("ix_jobs_status_department", "status", "department_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False)
    hiring_request_id = db.Column(db.Integer, db.ForeignKey("hiring_requests.id", ondelete="SET NULL"))
    description = db.Column(db.Text, nullable=False)
    required_skills = db.Column(db.JSON, nullable=False, default=list)
    preferred_skills = db.Column(db.JSON, nullable=False, default=list)
    qualifications = db.Column(db.Text)
    experience_min_years = db.Column(db.Integer)
    experience_max_years = db.Column(db.Integer)
    location = db.Column(db.String(120))
    is_remote = db.Column(db.Boolean, nullable=False, default=False)
    employment_type = db.Column(db.String(20), nullable=False, default="full_time")
    headcount = db.Column(db.Integer, nullable=False, default=1)
    application_deadline = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="draft")
    recruiter_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    hiring_manager_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    published_at = db.Column(db.DateTime(timezone=True))
    closed_at = db.Column(db.DateTime(timezone=True))

    department = db.relationship("Department")
    hiring_request = db.relationship("HiringRequest", back_populates="jobs")
    recruiter = db.relationship("User", foreign_keys=[recruiter_id])
    hiring_manager = db.relationship("User", foreign_keys=[hiring_manager_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    applications = db.relationship("Application", back_populates="job", lazy="dynamic")

    @property
    def employment_type_label(self) -> str:
        return EMPLOYMENT_TYPE_LABELS.get(self.employment_type, self.employment_type)

    def is_open_for_applications(self, today=None) -> bool:
        from app.models.base import today_utc

        today = today or today_utc()
        return self.status == "published" and (self.application_deadline is None or self.application_deadline >= today)


# ---------------------------------------------------------------------------
# Candidates & resumes
# ---------------------------------------------------------------------------
class Candidate(TimestampMixin, db.Model):
    __tablename__ = "candidates"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    phone = db.Column(db.String(40))
    location = db.Column(db.String(120))
    headline = db.Column(db.String(200))
    summary = db.Column(db.Text)
    years_experience = db.Column(db.Numeric(4, 1))
    skills = db.Column(db.JSON, nullable=False, default=list)
    education = db.Column(db.JSON, nullable=False, default=list)
    certifications = db.Column(db.JSON, nullable=False, default=list)
    linkedin_url = db.Column(db.String(255))
    portfolio_url = db.Column(db.String(255))

    user = db.relationship("User", back_populates="candidate")
    resumes = db.relationship(
        "Resume", back_populates="candidate", cascade="all, delete-orphan", order_by="Resume.created_at.desc()"
    )
    applications = db.relationship("Application", back_populates="candidate", lazy="dynamic")

    @property
    def primary_resume(self):
        for resume in self.resumes:
            if resume.is_primary:
                return resume
        return self.resumes[0] if self.resumes else None


RESUME_PARSE_STATUSES = ("pending", "parsed", "needs_ocr", "failed")


class Resume(TimestampMixin, db.Model):
    __tablename__ = "resumes"
    __table_args__ = (status_check("parse_status", RESUME_PARSE_STATUSES, "parse_status_valid"),)

    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(80), nullable=False, unique=True)
    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    extracted_text = db.Column(db.Text)
    parse_status = db.Column(db.String(20), nullable=False, default="pending")
    parse_error = db.Column(db.String(255))
    extracted = db.Column(db.JSON, nullable=False, default=dict)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)

    candidate = db.relationship("Candidate", back_populates="resumes")


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
APPLICATION_STATUSES = (
    "submitted", "under_review", "on_hold", "further_review", "shortlisted", "interviewing",
    "approved_for_offer", "offered", "hired", "rejected", "withdrawn", "offer_declined",
)
APPLICATION_TERMINAL = frozenset({"hired", "rejected", "withdrawn", "offer_declined"})
APPLICATION_TRANSITIONS = {
    "submitted": {"under_review", "rejected", "withdrawn"},
    "under_review": {"shortlisted", "on_hold", "further_review", "rejected", "withdrawn"},
    "on_hold": {"under_review", "shortlisted", "further_review", "rejected", "withdrawn"},
    "further_review": {"shortlisted", "on_hold", "rejected", "withdrawn"},
    "shortlisted": {"interviewing", "on_hold", "rejected", "withdrawn"},
    "interviewing": {"approved_for_offer", "rejected", "withdrawn"},
    "approved_for_offer": {"offered", "rejected", "withdrawn"},
    "offered": {"hired", "offer_declined", "approved_for_offer"},
    "hired": set(), "rejected": set(), "withdrawn": set(), "offer_declined": set(),
}
# Statuses a reviewer may set manually; the rest are set by workflow actions.
MANUAL_REVIEW_STATUSES = ("under_review", "shortlisted", "on_hold", "further_review", "rejected")
APPLICATION_STATUS_LABELS = {
    "submitted": "Submitted", "under_review": "Under review", "on_hold": "On hold",
    "further_review": "Further review", "shortlisted": "Shortlisted", "interviewing": "Interviewing",
    "approved_for_offer": "Approved for offer", "offered": "Offer extended", "hired": "Hired",
    "rejected": "Not progressed", "withdrawn": "Withdrawn", "offer_declined": "Offer declined",
}


class Application(TimestampMixin, db.Model):
    __tablename__ = "applications"
    __table_args__ = (
        db.UniqueConstraint("candidate_id", "job_id", name="uq_applications_candidate_job"),
        status_check("status", APPLICATION_STATUSES),
        db.Index("ix_applications_job_status", "job_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    resume_id = db.Column(db.Integer, db.ForeignKey("resumes.id", ondelete="SET NULL"))
    cover_letter = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="submitted")
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    status_changed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    candidate = db.relationship("Candidate", back_populates="applications")
    job = db.relationship("Job", back_populates="applications")
    resume = db.relationship("Resume")
    events = db.relationship(
        "ApplicationEvent", back_populates="application", cascade="all, delete-orphan",
        order_by="ApplicationEvent.created_at",
    )
    screenings = db.relationship(
        "ScreeningResult", back_populates="application", cascade="all, delete-orphan",
        order_by="ScreeningResult.created_at.desc()",
    )
    interviews = db.relationship("Interview", back_populates="application", order_by="Interview.scheduled_at")
    decisions = db.relationship("HiringDecision", back_populates="application", order_by="HiringDecision.created_at")
    offers = db.relationship("Offer", back_populates="application", order_by="Offer.created_at.desc()")

    @property
    def status_label(self) -> str:
        return APPLICATION_STATUS_LABELS.get(self.status, self.status)

    @property
    def latest_screening(self):
        return self.screenings[0] if self.screenings else None

    @property
    def current_offer(self):
        return self.offers[0] if self.offers else None


class ApplicationEvent(db.Model):
    """Immutable status history for an application (powers the timeline)."""

    __tablename__ = "application_events"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = db.Column(db.String(30))
    to_status = db.Column(db.String(30), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reason = db.Column(db.Text)
    # Internal notes are hidden from the candidate timeline.
    internal = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    application = db.relationship("Application", back_populates="events")
    actor = db.relationship("User")


# ---------------------------------------------------------------------------
# Screening (rule-based + optional AI) — always subject to human review
# ---------------------------------------------------------------------------
class ScreeningResult(TimestampMixin, db.Model):
    __tablename__ = "screening_results"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    matched_skills = db.Column(db.JSON, nullable=False, default=list)
    missing_skills = db.Column(db.JSON, nullable=False, default=list)
    evidence = db.Column(db.JSON, nullable=False, default=list)
    skill_overlap = db.Column(db.Float)  # % of required skills evidenced or claimed — NOT a hiring probability
    text_similarity = db.Column(db.Float)  # TF-IDF cosine similarity resume <-> job text
    experience_summary = db.Column(db.Text)
    ai_summary = db.Column(db.Text)
    verification_questions = db.Column(db.JSON, nullable=False, default=list)
    source = db.Column(db.String(20), nullable=False, default="rules")
    ai_model = db.Column(db.String(100))
    ai_error = db.Column(db.String(255))
    analysis_version = db.Column(db.String(20), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    # Human review / override
    reviewer_notes = db.Column(db.Text)
    corrected_matched_skills = db.Column(db.JSON)
    corrected_missing_skills = db.Column(db.JSON)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at = db.Column(db.DateTime(timezone=True))

    application = db.relationship("Application", back_populates="screenings")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    @property
    def effective_matched(self):
        return self.corrected_matched_skills if self.corrected_matched_skills is not None else self.matched_skills

    @property
    def effective_missing(self):
        return self.corrected_missing_skills if self.corrected_missing_skills is not None else self.missing_skills


# ---------------------------------------------------------------------------
# Job clustering runs
# ---------------------------------------------------------------------------
CLUSTER_RUN_STATUSES = ("completed", "insufficient_data", "failed")


class JobClusterRun(TimestampMixin, db.Model):
    __tablename__ = "job_cluster_runs"
    __table_args__ = (status_check("status", CLUSTER_RUN_STATUSES),)

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(30), nullable=False)
    algorithm = db.Column(db.String(60), nullable=False, default="tfidf+kmeans")
    n_jobs = db.Column(db.Integer, nullable=False, default=0)
    n_clusters = db.Column(db.Integer, nullable=False, default=0)
    silhouette = db.Column(db.Float)
    params = db.Column(db.JSON, nullable=False, default=dict)
    clusters = db.Column(db.JSON, nullable=False, default=list)
    k_scores = db.Column(db.JSON, nullable=False, default=list)
    fingerprint = db.Column(db.String(64), nullable=False)
    message = db.Column(db.String(255))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))

    assignments = db.relationship("JobClusterAssignment", back_populates="run", cascade="all, delete-orphan")
    created_by = db.relationship("User")


class JobClusterAssignment(db.Model):
    __tablename__ = "job_cluster_assignments"
    __table_args__ = (db.UniqueConstraint("run_id", "job_id", name="uq_cluster_assignment_run_job"),)

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("job_cluster_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_index = db.Column(db.Integer, nullable=False)
    centroid_similarity = db.Column(db.Float)

    run = db.relationship("JobClusterRun", back_populates="assignments")
    job = db.relationship("Job")
