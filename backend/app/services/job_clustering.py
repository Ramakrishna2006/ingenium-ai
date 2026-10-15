"""Multi-job clustering (TF-IDF + K-Means) and similar-job recommendations.

Only vacancy text is used (title, description, skills, department, employment
type, qualifications) — never candidate data. Cluster numbers are arbitrary
group identifiers, NOT rankings.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

import numpy as np
import sqlalchemy as sa
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity

from app.extensions import db
from app.models import Job, JobClusterAssignment, JobClusterRun

log = logging.getLogger(__name__)

RANDOM_STATE = 42
MAX_K = 8
TOKEN_PATTERN = r"(?u)\b[a-zA-Z][a-zA-Z0-9+#]*"
# Generic job-ad words that carry no information about the type of role.
DOMAIN_STOP_WORDS = {
    "job", "role", "team", "work", "working", "candidate", "candidates", "experience", "years", "year",
    "ability", "strong", "good", "excellent", "skills", "skill", "knowledge", "responsibilities",
    "requirements", "required", "preferred", "opportunity", "join", "company", "looking", "ideal",
    "time", "full", "part", "including", "using", "use", "help", "new", "plus", "etc", "day", "will",
}


def _stop_words():
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return list(ENGLISH_STOP_WORDS.union(DOMAIN_STOP_WORDS))


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9+#\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def job_document(job: Job) -> str:
    """Controlled combination of vacancy fields. Title and skills are repeated to weight them higher."""
    skills = " ".join(job.required_skills or []) + " " + " ".join(job.preferred_skills or [])
    parts = [
        job.title or "", job.title or "",
        skills, skills,
        job.description or "",
        job.qualifications or "",
        job.department.name if job.department else "",
        (job.employment_type or "").replace("_", " "),
    ]
    return normalize_text(" ".join(parts))


def _vectorizer() -> TfidfVectorizer:
    return TfidfVectorizer(stop_words=_stop_words(), token_pattern=TOKEN_PATTERN, ngram_range=(1, 2),
                           min_df=1, sublinear_tf=True, max_features=5000)


@dataclass
class ClusterOutcome:
    status: str
    message: str
    algorithm: str = "tfidf+kmeans"
    n_clusters: int = 0
    silhouette: float | None = None
    k_scores: list = field(default_factory=list)
    clusters: list = field(default_factory=list)  # [{index,label,keywords,size,job_ids}]
    assignments: dict = field(default_factory=dict)  # job_id -> (cluster_index, centroid_similarity)


def _label(keywords: list[str]) -> str:
    picked = []
    for kw in keywords:
        if not any(kw in p or p in kw for p in picked):
            picked.append(kw)
        if len(picked) == 3:
            break
    return " · ".join(w.title() for w in picked) if picked else "General"


def _top_terms(vector, terms, n=8) -> list[str]:
    vector = np.asarray(vector).ravel()
    order = vector.argsort()[::-1]
    return [terms[i] for i in order[:n] if vector[i] > 0]


def cluster_documents(docs: list[tuple[int, str]], max_k: int = MAX_K, random_state: int = RANDOM_STATE) -> ClusterOutcome:
    """Pure function: cluster (job_id, normalised_text) pairs. Handles every small/degenerate case."""
    if max_k < 2:
        raise ValueError("max_k must be at least 2")
    if not docs:
        return ClusterOutcome(status="insufficient_data", message="There are no published jobs to cluster.")

    # Collapse exact duplicates so they cannot distort K-Means; they share a cluster.
    distinct_texts: list[str] = []
    members: list[list[int]] = []
    index_of: dict[str, int] = {}
    for job_id, text in docs:
        key = text.strip()
        if key not in index_of:
            index_of[key] = len(distinct_texts)
            distinct_texts.append(key)
            members.append([])
        members[index_of[key]].append(job_id)
    d = len(distinct_texts)

    try:
        vec = _vectorizer()
        X = vec.fit_transform(distinct_texts)
        terms = vec.get_feature_names_out()
    except ValueError:
        # Empty vocabulary (e.g. very short / stop-word-only text): one fallback group.
        outcome = ClusterOutcome(status="insufficient_data", algorithm="fallback-single-group", n_clusters=1,
                                 message="Job texts are too short to build a vocabulary; jobs were placed in one group.")
        outcome.clusters = [{"index": 0, "label": "All jobs", "keywords": [], "size": len(docs),
                             "job_ids": [j for j, _ in docs]}]
        outcome.assignments = {j: (0, None) for j, _ in docs}
        return outcome

    if d < 3:
        labels = np.zeros(d, dtype=int)
        message = "Only one distinct job text — a single group was created."
        if d == 2:
            sim = float(cosine_similarity(X[0], X[1])[0, 0])
            if sim < 0.2:
                labels = np.array([0, 1])
                message = "Two dissimilar jobs — each forms its own group. Add more jobs for meaningful clustering."
            else:
                message = "Two similar jobs — grouped together. Add more jobs for meaningful clustering."
        return _build(labels, X, terms, members, docs, ClusterOutcome(status="insufficient_data", message=message))

    upper = min(max_k, d - 1)
    best = None
    k_scores = []
    for k in range(2, upper + 1):
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state)
        labels = km.fit_predict(X)
        if len(set(labels)) < 2:
            k_scores.append({"k": k, "silhouette": None})
            continue
        try:
            score = float(silhouette_score(X, labels, metric="cosine"))
        except ValueError:
            score = None
        k_scores.append({"k": k, "silhouette": None if score is None else round(score, 4)})
        if score is not None and (best is None or score > best[0] + 1e-9):
            best = (score, k, labels)

    if best is None:
        labels = np.zeros(d, dtype=int)
        outcome = ClusterOutcome(status="insufficient_data", k_scores=k_scores,
                                 message="Vocabulary too sparse to separate jobs; a single group was created.")
        return _build(labels, X, terms, members, docs, outcome)

    score, k, labels = best
    quality = "good" if score >= 0.25 else "moderate" if score >= 0.1 else "weak"
    outcome = ClusterOutcome(
        status="completed", silhouette=round(score, 4), k_scores=k_scores,
        message=f"{k} groups selected by best silhouette score ({score:.3f}, {quality} separation).",
    )
    return _build(labels, X, terms, members, docs, outcome)


def _build(labels, X, terms, members, docs, outcome: ClusterOutcome) -> ClusterOutcome:
    labels = np.asarray(labels)
    # Renumber clusters 0..n-1 in order of size (largest first) for stable display.
    unique, counts = np.unique(labels, return_counts=True)
    sizes = {int(u): int(sum(len(members[i]) for i in np.where(labels == u)[0])) for u in unique}
    order = sorted(sizes, key=lambda u: (-sizes[u], u))
    remap = {old: new for new, old in enumerate(order)}
    clusters = []
    for old in order:
        rows = np.where(labels == old)[0]
        centroid = np.asarray(X[rows].mean(axis=0))
        keywords = _top_terms(centroid, terms)
        job_ids = [jid for r in rows for jid in members[r]]
        sims = cosine_similarity(X[rows], centroid).ravel() if X.shape[1] else np.zeros(len(rows))
        for r, sim in zip(rows, sims):
            for jid in members[r]:
                outcome.assignments[jid] = (remap[old], round(float(sim), 4))
        clusters.append({"index": remap[old], "label": _label(keywords), "keywords": keywords,
                         "size": len(job_ids), "job_ids": job_ids})
    outcome.clusters = sorted(clusters, key=lambda c: c["index"])
    outcome.n_clusters = len(clusters)
    return outcome


# ----------------------------------------------------------------- persistence
def published_jobs() -> list[Job]:
    return list(db.session.scalars(sa.select(Job).where(Job.status == "published").order_by(Job.id)))


def jobs_fingerprint(jobs: list[Job]) -> str:
    h = hashlib.sha256()
    for job in jobs:
        h.update(f"{job.id}|{job_document(job)}\n".encode())
    return h.hexdigest()


def latest_run() -> JobClusterRun | None:
    return db.session.scalar(sa.select(JobClusterRun).order_by(JobClusterRun.id.desc()).limit(1))


def is_stale(run: JobClusterRun | None = None) -> bool:
    run = run or latest_run()
    return run is None or run.fingerprint != jobs_fingerprint(published_jobs())


def recompute_clusters(user_id: int | None = None, max_k: int = MAX_K) -> JobClusterRun:
    jobs = published_jobs()
    docs = [(j.id, job_document(j)) for j in jobs]
    try:
        outcome = cluster_documents(docs, max_k=max_k)
    except Exception as exc:  # never crash the dashboard because of ML errors
        log.exception("Clustering failed")
        outcome = ClusterOutcome(status="failed", message=f"Clustering failed: {type(exc).__name__}")
    run = JobClusterRun(
        status=outcome.status, algorithm=outcome.algorithm, n_jobs=len(jobs), n_clusters=outcome.n_clusters,
        silhouette=outcome.silhouette, clusters=outcome.clusters, k_scores=outcome.k_scores,
        params={"max_k": max_k, "random_state": RANDOM_STATE, "ngram_range": [1, 2], "metric": "cosine"},
        fingerprint=jobs_fingerprint(jobs), message=outcome.message[:255], created_by_id=user_id,
    )
    db.session.add(run)
    db.session.flush()
    for job_id, (idx, sim) in outcome.assignments.items():
        db.session.add(JobClusterAssignment(run_id=run.id, job_id=job_id, cluster_index=idx, centroid_similarity=sim))
    return run


def cluster_of(job_id: int, run: JobClusterRun | None = None) -> dict | None:
    run = run or latest_run()
    if not run:
        return None
    for c in run.clusters or []:
        if job_id in c.get("job_ids", []):
            return c
    return None


# ------------------------------------------------------------ similar jobs
_similarity_cache: dict[str, tuple] = {}


def _similarity_matrix(jobs: list[Job]):
    key = jobs_fingerprint(jobs)
    cached = _similarity_cache.get(key)
    if cached:
        return cached
    docs = [job_document(j) for j in jobs]
    try:
        vec = _vectorizer()
        X = vec.fit_transform(docs)
        result = (vec, X, cosine_similarity(X))
    except ValueError:
        result = (None, None, None)
    if len(_similarity_cache) > 16:
        _similarity_cache.clear()
    _similarity_cache[key] = result
    return result


def similar_jobs(job: Job, top_n: int = 5, min_score: float = 0.05) -> list[dict]:
    """Published jobs most similar to ``job`` by TF-IDF cosine similarity."""
    pool = published_jobs()
    if job not in pool:
        pool = pool + [job]
    if len(pool) < 2:
        return []
    _vec, _X, sims = _similarity_matrix(pool)
    if sims is None:
        return []
    idx = pool.index(job)
    run = latest_run()
    own_cluster = cluster_of(job.id, run)
    results = []
    for j, score in sorted(enumerate(sims[idx]), key=lambda t: -t[1]):
        other = pool[j]
        if other.id == job.id or other.status != "published" or score < min_score:
            continue
        shared = sorted(set(map(str.lower, job.required_skills or [])) & set(map(str.lower, other.required_skills or [])))
        other_cluster = cluster_of(other.id, run)
        results.append({
            "job": other, "similarity": round(float(score), 3), "shared_skills": shared,
            "same_cluster": bool(own_cluster and other_cluster and own_cluster["index"] == other_cluster["index"]),
        })
        if len(results) >= top_n:
            break
    return results


def text_similarity_to_jobs(text: str, jobs: list[Job]) -> dict[int, float]:
    """Cosine similarity between free text (e.g. a resume) and each job, IDF fitted on published jobs + text."""
    if not text or not jobs:
        return {j.id: 0.0 for j in jobs}
    corpus_jobs = {j.id: j for j in published_jobs()}
    for j in jobs:
        corpus_jobs.setdefault(j.id, j)
    ordered = list(corpus_jobs.values())
    try:
        vec = _vectorizer()
        X = vec.fit_transform([job_document(j) for j in ordered] + [normalize_text(text)])
    except ValueError:
        return {j.id: 0.0 for j in jobs}
    sims = cosine_similarity(X[-1], X[:-1]).ravel()
    by_id = {j.id: round(float(s), 3) for j, s in zip(ordered, sims)}
    return {j.id: by_id.get(j.id, 0.0) for j in jobs}
