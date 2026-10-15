"""Explicit JSON representations. Models are never dumped wholesale."""
from __future__ import annotations

from app.models.base import as_utc


def iso(value):
    if value is None:
        return None
    if hasattr(value, "tzinfo"):
        return as_utc(value).isoformat()
    return value.isoformat()


def num(value):
    return None if value is None else float(value)


def user_json(u):
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "active": u.active}


def department_json(d):
    return {"id": d.id, "name": d.name, "code": d.code, "description": d.description, "location": d.location,
            "head_user_id": d.head_user_id, "is_active": d.is_active}


def hiring_request_json(h):
    return {"id": h.id, "title": h.title, "department_id": h.department_id, "hiring_manager_id": h.hiring_manager_id,
            "headcount": h.headcount, "employment_type": h.employment_type, "justification": h.justification,
            "target_date": iso(h.target_date), "budget_amount": num(h.budget_amount), "budget_currency": h.budget_currency,
            "status": h.status, "decision_notes": h.decision_notes, "created_at": iso(h.created_at)}


def job_json(j, internal: bool = False):
    data = {"id": j.id, "title": j.title, "department": {"id": j.department_id, "name": j.department.name if j.department else None},
            "description": j.description, "required_skills": j.required_skills, "preferred_skills": j.preferred_skills,
            "qualifications": j.qualifications, "experience_min_years": j.experience_min_years,
            "experience_max_years": j.experience_max_years, "location": j.location, "is_remote": j.is_remote,
            "employment_type": j.employment_type, "application_deadline": iso(j.application_deadline),
            "published_at": iso(j.published_at)}
    if internal:
        data.update({"status": j.status, "headcount": j.headcount, "recruiter_id": j.recruiter_id,
                     "hiring_manager_id": j.hiring_manager_id, "hiring_request_id": j.hiring_request_id,
                     "created_at": iso(j.created_at)})
    return data


def resume_json(r):
    return {"id": r.id, "original_filename": r.original_filename, "size_bytes": r.size_bytes,
            "parse_status": r.parse_status, "parse_error": r.parse_error, "extracted": r.extracted,
            "is_primary": r.is_primary, "uploaded_at": iso(r.created_at)}


def candidate_json(c, limited: bool = False):
    data = {"id": c.id, "full_name": c.user.full_name, "headline": c.headline, "summary": c.summary,
            "years_experience": num(c.years_experience), "skills": c.skills, "education": c.education,
            "certifications": c.certifications, "resumes": [resume_json(r) for r in c.resumes]}
    if not limited:
        data.update({"email": c.user.email, "phone": c.phone, "location": c.location,
                     "linkedin_url": c.linkedin_url, "portfolio_url": c.portfolio_url})
    return data


def event_json(e, include_reason: bool = True):
    return {"from_status": e.from_status, "to_status": e.to_status, "at": iso(e.created_at),
            "reason": e.reason if include_reason else None}


def application_json(a, staff: bool = True):
    data = {"id": a.id, "job": {"id": a.job_id, "title": a.job.title}, "candidate_id": a.candidate_id,
            "status": a.status, "status_label": a.status_label, "submitted_at": iso(a.submitted_at),
            "status_changed_at": iso(a.status_changed_at), "resume_id": a.resume_id}
    if staff:
        data["candidate_name"] = a.candidate.user.full_name
        data["history"] = [event_json(e) for e in a.events]
    else:
        data["history"] = [event_json(e, include_reason=e.actor_id == a.candidate.user_id) for e in a.events if not e.internal]
    return data


def screening_json(s):
    return {"id": s.id, "application_id": s.application_id, "matched_skills": s.matched_skills,
            "missing_skills": s.missing_skills, "effective_matched_skills": s.effective_matched,
            "effective_missing_skills": s.effective_missing, "evidence": s.evidence, "skill_overlap": s.skill_overlap,
            "text_similarity": s.text_similarity, "explanation": s.experience_summary, "ai_summary": s.ai_summary,
            "verification_questions": s.verification_questions, "source": s.source, "ai_model": s.ai_model,
            "ai_error": s.ai_error, "analysis_version": s.analysis_version, "created_at": iso(s.created_at),
            "reviewer_notes": s.reviewer_notes, "reviewed_by_id": s.reviewed_by_id, "reviewed_at": iso(s.reviewed_at),
            "disclaimer": "Evidence summary for human review. Not a hiring recommendation or probability."}


def decision_json(d):
    return {"id": d.id, "decision": d.decision, "rationale": d.rationale, "decided_by_id": d.decided_by_id,
            "created_at": iso(d.created_at)}


def interview_json(i, include_notes: bool = True):
    data = {"id": i.id, "application_id": i.application_id, "kind": i.kind, "status": i.status,
            "scheduled_at_utc": iso(i.scheduled_at), "timezone": i.timezone, "duration_minutes": i.duration_minutes,
            "location": i.location, "meeting_link": i.meeting_link, "reschedule_count": i.reschedule_count,
            "interviewer_ids": sorted(i.interviewer_ids), "job_title": i.application.job.title}
    if include_notes:
        data.update({"notes": i.notes, "question_bank": i.question_bank})
    return data


def feedback_json(f):
    return {"id": f.id, "interviewer_id": f.interviewer_id, "ratings": f.ratings, "overall_rating": f.overall_rating,
            "recommendation": f.recommendation, "strengths": f.strengths, "concerns": f.concerns,
            "notes": f.notes, "submitted_at": iso(f.created_at)}


def offer_json(o, staff: bool = True):
    data = {"id": o.id, "application_id": o.application_id, "designation": o.designation,
            "department": o.department.name, "salary": num(o.salary), "currency": o.currency,
            "joining_date": iso(o.joining_date), "expires_on": iso(o.expires_on), "terms": o.terms,
            "status": o.status, "version": o.version, "extended_at": iso(o.extended_at),
            "responded_at": iso(o.responded_at), "has_pdf": bool(o.pdf_stored_filename)}
    if staff:
        data["approvals"] = [{"decision": a.decision, "approver_id": a.approver_id, "comments": a.comments,
                              "version": a.offer_version, "at": iso(a.created_at)} for a in o.approvals]
    return data


def employee_json(e):
    return {"id": e.id, "employee_code": e.employee_code, "user_id": e.user_id, "full_name": e.user.full_name,
            "department": {"id": e.department_id, "name": e.department.name}, "manager_id": e.manager_id,
            "designation": e.designation, "employment_type": e.employment_type, "joining_date": iso(e.joining_date),
            "probation_end_date": iso(e.probation_end_date), "employment_status": e.employment_status,
            "onboarding_progress": e.onboarding_progress, "application_id": e.application_id, "offer_id": e.offer_id}


def document_json(d):
    return {"id": d.id, "employee_id": d.employee_id, "doc_type": d.doc_type, "title": d.title, "required": d.required,
            "status": d.status, "original_filename": d.original_filename, "submitted_at": iso(d.submitted_at),
            "verified_at": iso(d.verified_at), "review_notes": d.review_notes}


def task_json(t):
    return {"id": t.id, "employee_id": t.employee_id, "title": t.title, "description": t.description,
            "category": t.category, "assignee_type": t.assignee_type, "assignee_user_id": t.assignee_user_id,
            "due_date": iso(t.due_date), "priority": t.priority, "status": t.status, "blocked_reason": t.blocked_reason,
            "completed_at": iso(t.completed_at)}


def training_json(t):
    return {"id": t.id, "employee_id": t.employee_id, "title": t.title, "description": t.description,
            "resource_url": t.resource_url, "due_date": iso(t.due_date), "is_mandatory": t.is_mandatory,
            "status": t.status, "completed_at": iso(t.completed_at)}


def notification_json(n):
    return {"id": n.id, "title": n.title, "body": n.body, "link": n.link, "category": n.category,
            "read": n.read_at is not None, "created_at": iso(n.created_at)}


def cluster_run_json(run, stale: bool):
    return {"id": run.id, "status": run.status, "algorithm": run.algorithm, "n_jobs": run.n_jobs,
            "n_clusters": run.n_clusters, "silhouette": run.silhouette, "k_scores": run.k_scores,
            "params": run.params, "message": run.message, "created_at": iso(run.created_at), "stale": stale,
            "clusters": run.clusters,
            "note": "Cluster indexes are arbitrary group identifiers, not rankings."}
