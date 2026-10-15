"""Job clustering + similarity: edge cases, determinism and API authorization."""
from __future__ import annotations

import io

import pytest

from app.extensions import db
from app.services.job_clustering import cluster_documents, recompute_clusters, similar_jobs
from tests.conftest import RESUME_TEXT, docx_bytes, login

TECH = [
    (1, "backend engineer python flask postgresql rest apis docker microservices"),
    (2, "senior backend developer python django postgresql apis services"),
    (3, "data scientist machine learning statistics python pandas scikit-learn models"),
    (4, "machine learning engineer deep learning pytorch nlp models training"),
    (5, "devops engineer aws kubernetes terraform ci cd pipelines infrastructure"),
    (6, "cloud platform engineer azure kubernetes terraform linux infrastructure"),
    (7, "hr generalist recruitment onboarding employee relations labour law"),
    (8, "talent acquisition specialist recruitment onboarding interviews offers"),
]


def test_zero_jobs():
    out = cluster_documents([])
    assert out.status == "insufficient_data" and out.n_clusters == 0


def test_one_job():
    out = cluster_documents([(1, "backend engineer python flask")])
    assert out.n_clusters == 1 and out.assignments[1][0] == 0


def test_two_jobs_do_not_crash():
    out = cluster_documents(TECH[:2])
    assert out.n_clusters >= 1 and set(out.assignments) == {1, 2}


def test_duplicate_descriptions_share_a_cluster():
    docs = [(1, "python flask api"), (2, "python flask api"), (3, "python flask api")]
    out = cluster_documents(docs)
    assert len({out.assignments[j][0] for j in (1, 2, 3)}) == 1


def test_empty_and_tiny_texts_fall_back_gracefully():
    out = cluster_documents([(1, ""), (2, "a"), (3, "the and of")])
    assert out.status == "insufficient_data" and out.n_clusters == 1


def test_many_jobs_form_interpretable_groups_deterministically():
    a, b = cluster_documents(TECH), cluster_documents(TECH)
    assert a.status == "completed" and 2 <= a.n_clusters <= 6
    assert a.assignments == b.assignments  # reproducible random_state
    assert a.silhouette is not None
    # Clearly related roles land together
    assert a.assignments[7][0] == a.assignments[8][0]
    assert a.assignments[5][0] == a.assignments[6][0]
    assert a.assignments[1][0] != a.assignments[7][0]
    assert all(c["keywords"] and c["label"] for c in a.clusters)


def test_invalid_k_configuration_is_rejected_without_crashing(app, factory):
    with pytest.raises(ValueError):
        cluster_documents(TECH, max_k=1)
    factory.job()
    run = recompute_clusters(max_k=1)  # persisted as a failed run, page still works
    assert run.status == "failed" and "ValueError" in run.message


def test_recompute_persists_run_and_similar_jobs(app, factory):
    dept = factory.department()
    backend = factory.job(dept, "Backend Engineer", ("Python", "Flask", "PostgreSQL"))
    factory.job(dept, "Python API Developer", ("Python", "Flask", "REST APIs"))
    factory.job(dept, "DevOps Engineer", ("AWS", "Kubernetes", "Terraform"),
                description="Operate Kubernetes on AWS with Terraform and CI/CD pipelines.")
    factory.job(dept, "Draft Role", ("Python",), status="draft")  # drafts are excluded
    run = recompute_clusters()
    db.session.commit()
    assert run.n_jobs == 3
    sims = similar_jobs(backend)
    assert sims and sims[0]["job"].title == "Python API Developer"
    assert all(0 <= s["similarity"] <= 1 for s in sims)


def test_clustering_api_authorization_and_shape(client, factory):
    rec = factory.user("recruiter")
    for i, (_, text) in enumerate(TECH):
        factory.job(title=f"Role {i}", skills=text.split()[:3], description=text + " team collaboration delivery")
    for role, code in (("interviewer", 403), ("candidate", 403)):
        login(client, factory.user(role))
        assert client.post("/api/v1/jobs/clusters/recompute").status_code == code
        assert client.get("/api/v1/jobs/clusters").status_code == code
    login(client, rec)
    assert client.post("/api/v1/jobs/clusters/recompute").status_code in (200, 201)
    run = client.get("/api/v1/jobs/clusters").get_json()["run"]
    assert run["n_jobs"] == len(TECH) and "not rankings" in run["note"]
    first = run["clusters"][0]
    members = client.get(f"/api/v1/jobs/clusters/{first['index']}/jobs").get_json()
    assert len(members["jobs"]) == first["size"]
    assert client.get("/api/v1/jobs/clusters/99/jobs").status_code == 404


def test_candidate_multi_job_comparison_keeps_concepts_separate(client, factory):
    rec = factory.user("hr_manager")
    py = factory.job(title="Python Developer", skills=("Python", "Flask", "Kubernetes"))
    ops = factory.job(title="DevOps", skills=("AWS", "Kubernetes", "Terraform"))
    cand = factory.user("candidate")
    login(client, cand)
    client.post("/me/resumes", data={"resume": (io.BytesIO(docx_bytes(RESUME_TEXT)), "r.docx")},
                content_type="multipart/form-data")
    login(client, rec)
    resp = client.post(f"/api/v1/candidates/{cand.candidate.id}/job-comparison", json={"job_ids": [py.id, ops.id]})
    assert resp.status_code == 200
    body = resp.get_json()
    by_job = {r["job_id"]: r for r in body["rows"]}
    assert "Python" in by_job[py.id]["matched_skills"] and "Flask" in by_job[py.id]["matched_skills"]
    assert "Terraform" in by_job[ops.id]["missing_skills"]
    for r in by_job.values():
        assert "skill_overlap" in r and "text_similarity" in r and "hire" not in str(r.keys()).lower()
