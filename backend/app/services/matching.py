"""Explainable candidate <-> job skill comparison.

Four concepts are kept separate on purpose:
  * cluster membership (job_clustering) — which group a vacancy belongs to;
  * text similarity — TF-IDF cosine between resume text and job text;
  * skill overlap — share of the job's *explicit* required skills that the
    candidate documents (evidenced in the resume or claimed in the profile);
  * the recruiter's decision — always a human action.
None of these numbers is a hiring probability.
"""
from __future__ import annotations

from app.models import Candidate, Job
from app.services.job_clustering import cluster_of, latest_run, published_jobs, text_similarity_to_jobs
from app.services.skills import evidence_snippet, find_skill_mentions, normalize_skill_list

ANALYSIS_VERSION = "rules-1.2"


def candidate_text(candidate: Candidate, resume=None) -> str:
    """Professional text only — no name, email or phone."""
    resume = resume or candidate.primary_resume
    parts = [candidate.headline or "", candidate.summary or "", " ".join(candidate.skills or [])]
    if resume is not None and resume.extracted_text:
        parts.append(resume.extracted_text)
    return "\n".join(p for p in parts if p)


def candidate_skill_set(candidate: Candidate, resume=None) -> list[str]:
    resume = resume or candidate.primary_resume
    extracted = (resume.extracted or {}).get("skills", []) if resume is not None else []
    return normalize_skill_list(list(candidate.skills or []) + list(extracted))


def compare_to_job(candidate: Candidate, job: Job, resume=None, similarity: float | None = None) -> dict:
    resume = resume or candidate.primary_resume
    resume_text = resume.extracted_text if resume is not None and resume.extracted_text else ""
    profile_skills = {s.lower() for s in candidate_skill_set(candidate, resume)}
    required = normalize_skill_list(job.required_skills or [])
    preferred = normalize_skill_list(job.preferred_skills or [])

    evidence, matched, claimed, missing = [], [], [], []
    for skill in required:
        spans = find_skill_mentions(resume_text, skill)
        if spans:
            matched.append(skill)
            evidence.append({"skill": skill, "status": "evidenced", "snippet": evidence_snippet(resume_text, spans[0])})
        elif skill.lower() in profile_skills:
            claimed.append(skill)
            evidence.append({"skill": skill, "status": "claimed", "snippet": "Listed in candidate profile; not found in resume text."})
        else:
            missing.append(skill)
            evidence.append({"skill": skill, "status": "not_evidenced", "snippet": ""})
    preferred_found = [s for s in preferred if find_skill_mentions(resume_text, s) or s.lower() in profile_skills]

    overlap = None
    if required:
        overlap = round(100 * (len(matched) + len(claimed)) / len(required), 1)

    years = candidate.years_experience
    if years is None and resume is not None:
        years = (resume.extracted or {}).get("years_experience")
    experience = "unknown"
    if years is not None and job.experience_min_years is not None:
        experience = "meets" if float(years) >= job.experience_min_years else "below"

    if similarity is None:
        similarity = text_similarity_to_jobs(candidate_text(candidate, resume), [job]).get(job.id, 0.0)

    cluster = cluster_of(job.id)
    return {
        "job_id": job.id,
        "job_title": job.title,
        "department": job.department.name if job.department else None,
        "required_skills": required,
        "matched_skills": matched,
        "claimed_skills": claimed,
        "missing_skills": missing,
        "preferred_found": preferred_found,
        "evidence": evidence,
        "skill_overlap": overlap,
        "text_similarity": similarity,
        "experience_check": experience,
        "candidate_years": float(years) if years is not None else None,
        "job_min_years": job.experience_min_years,
        "cluster": {"index": cluster["index"], "label": cluster["label"]} if cluster else None,
        "explanation": explain(required, matched, claimed, missing, experience, job.experience_min_years),
        "analysis_version": ANALYSIS_VERSION,
    }


def explain(required, matched, claimed, missing, experience, min_years) -> str:
    if not required:
        return "This job lists no explicit required skills, so skill overlap cannot be estimated."
    parts = [f"{len(matched)} of {len(required)} required skills are evidenced in the resume"
             + (f" ({', '.join(matched)})" if matched else "") + "."]
    if claimed:
        parts.append(f"{len(claimed)} listed in the profile but not found in the resume: {', '.join(claimed)}.")
    if missing:
        parts.append(f"Not evidenced: {', '.join(missing)}.")
    if experience == "meets":
        parts.append(f"Stated experience meets the {min_years}+ year expectation.")
    elif experience == "below":
        parts.append(f"Stated experience is below the {min_years}+ year expectation.")
    parts.append("Verify in interview; this is not a hiring recommendation.")
    return " ".join(parts)


def compare_to_jobs(candidate: Candidate, jobs: list[Job]) -> dict:
    text = candidate_text(candidate)
    sims = text_similarity_to_jobs(text, jobs)
    rows = [compare_to_job(candidate, job, similarity=sims.get(job.id)) for job in jobs]
    exclude = {j.id for j in jobs} | {a.job_id for a in candidate.applications}
    return {
        "candidate_id": candidate.id,
        "has_resume": candidate.primary_resume is not None,
        "rows": rows,
        "related_roles": related_roles(candidate, exclude),
        "cluster_run_id": getattr(latest_run(), "id", None),
        "notes": [
            "Skill overlap = share of explicit required skills documented by the candidate (resume or profile).",
            "Text similarity = TF-IDF cosine similarity between resume and job text (0-1).",
            "Neither value is a ranking of the candidate or a probability of being hired.",
        ],
    }


def related_roles(candidate: Candidate, exclude_ids: set[int], top_n: int = 5, min_score: float = 0.05) -> list[dict]:
    jobs = [j for j in published_jobs() if j.id not in exclude_ids and j.is_open_for_applications()]
    if not jobs:
        return []
    sims = text_similarity_to_jobs(candidate_text(candidate), jobs)
    ranked = sorted(jobs, key=lambda j: -sims.get(j.id, 0))
    out = []
    for job in ranked[:top_n]:
        if sims.get(job.id, 0) < min_score:
            continue
        out.append({"job_id": job.id, "title": job.title,
                    "department": job.department.name if job.department else None,
                    "text_similarity": sims[job.id]})
    return out
