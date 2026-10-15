# Ingenium AI — REST API (`/api/v1`)

## Conventions
- **Auth:** session cookie (same login as the web UI). Sign in with `POST /api/v1/auth/login`.
- **CSRF:** every state-changing request needs the `X-CSRFToken` header. Get a token from
  `GET /api/v1/auth/csrf` (or the `<meta name="csrf-token">` tag on any page).
- **Bodies:** JSON (`Content-Type: application/json`), except file uploads (`multipart/form-data`).
- **Validation:** Pydantic schemas in `app/schemas.py` — the same ones the HTML forms use.
- **Pagination:** list endpoints accept `?page=1&per_page=20` (max 100) and return
  `{"items": [...], "page": 1, "per_page": 20, "total": 42, "pages": 3}`.
- **Errors:** always `{"error": {"code": "...", "message": "...", "details": ...}}` — never a stack trace.

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request`, `csrf_failed` | Malformed body, missing/invalid CSRF token |
| 401 | `unauthenticated` | Not signed in |
| 403 | `forbidden` | Signed in but lacking the permission or record ownership |
| 404 | `not_found` | Unknown record or one outside your scope |
| 409 | `conflict`, `duplicate_application`, `invalid_transition`, `email_taken` | State conflicts |
| 413 | `payload_too_large` | Upload over `MAX_UPLOAD_MB` |
| 422 | `validation_error` | Field validation failed (`details` lists fields) |
| 429 | `rate_limited` | Too many attempts (login, register, uploads, AI calls) |
| 503 | `ai_offline`, `ai_model_missing`, `ai_bad_output`, `ai_timeout` … | Ollama offline, model missing, malformed or slow AI output (non-AI features keep working) |

**Roles key:** SA Super Admin · HR HR Manager · RC Recruiter · HM Hiring Manager · IV Interviewer ·
CA Candidate · EM Employee. "Scoped" means the record must also be in your scope (`app/access.py`): e.g. a
recruiter sees applications for vacancies they own; a hiring manager sees their vacancies and team; an
interviewer sees only assigned interviews with contact details hidden; candidates and employees see only their own records.

## Endpoints

### Health & auth
| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/health` | public | `{"status": "ok", "database": true}` (503 if DB down) |
| GET | `/auth/csrf` | public | `{"csrf_token": "..."}` |
| POST | `/auth/register` | public | `{full_name, email, password, consent: true}` → 201. Always creates a Candidate; any `role` is ignored. Rate limited 5/10 min. |
| POST | `/auth/login` | public | `{email, password, remember?}`. Rate limited (`LOGIN_RATE_LIMIT`). |
| POST | `/auth/logout` | signed in | |
| GET | `/auth/me` | signed in | `{user, permissions}` |
| GET | `/ai/status` | signed in | `{enabled, reachable, model, model_available, installed_models, latency_ms, error}` |

### Organisation & planning
| Method | Path | Who |
|---|---|---|
| GET | `/departments` | staff (`department.view`) |
| POST | `/departments` | SA, HR — `{name, code, description?, location?, head_user_id?, is_active}` |
| PATCH | `/departments/{id}` | SA, HR |
| GET | `/hiring-requests` | SA, HR, RC, HM |
| POST | `/hiring-requests` | SA, HR, RC, HM — `{title, department_id, headcount, employment_type, justification, target_date?, budget_amount?, budget_currency?}` |
| POST | `/hiring-requests/{id}/decision` | SA, HR — `{decision: approved\|rejected\|revision_requested, notes?}` |

### Jobs, clustering & matching
| Method | Path | Who |
|---|---|---|
| GET | `/jobs` | public: published jobs. Staff with `job.view_internal` add `?scope=all` (optionally `&status=`) to see every job. Filters: `q, department_id, employment_type` |
| GET | `/jobs/{id}` | as above |
| POST | `/jobs` | SA, HR, RC — JobIn: `{title, department_id, description (≥20 chars), required_skills[], preferred_skills[], qualifications?, experience_min_years?, experience_max_years?, location?, is_remote, employment_type, headcount, application_deadline?, recruiter_id?, hiring_manager_id?, hiring_request_id?}` |
| PATCH | `/jobs/{id}` | SA, HR, RC (scoped) |
| POST | `/jobs/{id}/status` | SA, HR, RC — `{status: draft\|published\|paused\|closed\|archived}` (validated transitions) |
| POST | `/jobs/draft-description` | SA, HR, RC — `{title, department, skills, notes}` → AI draft. 20/min |
| GET | `/jobs/clusters` | SA, HR, RC, HM — latest run: status, k, silhouette, k_scores, clusters (`index`, `label`, `keywords`, `size`, `job_ids`), `stale` |
| POST | `/jobs/clusters/recompute` | SA, HR, RC — 201 with the new run. 10/min |
| GET | `/jobs/clusters/{index}/jobs` | SA, HR, RC, HM — jobs in a cluster with centroid similarity; 404 if not in latest run |
| GET | `/jobs/{id}/similar` | SA, HR, RC, HM — TF-IDF cosine similar jobs, shared skills, `same_cluster` |
| POST | `/candidates/{id}/job-comparison` | SA, HR, RC, HM (scoped) — `{job_ids: [..≤10], explain_with_ai?}` → per-job matched / claimed / missing skills, skill overlap %, text similarity, experience check, evidence snippets, related roles |

Cluster index is an arbitrary group id, never a rank. Skill overlap and text similarity are separate signals
and are not a hiring probability.

### Candidates, resumes & applications
| Method | Path | Who |
|---|---|---|
| GET | `/candidates` | staff with `candidate.view` (scoped) |
| GET | `/candidates/{id}` | scoped staff, or the candidate themself |
| GET / PATCH | `/candidates/me` | CA, EM — CandidateProfileIn |
| POST | `/candidates/me/resumes` | CA, EM — multipart `resume` (PDF/DOCX) → 201 with parse status + extracted fields. 20/10 min |
| GET | `/applications` | candidates: own; staff: scoped. Filters `status, job_id` |
| POST | `/applications` | CA, EM — `{job_id, resume_id?, cover_letter?}`. 409 `duplicate_application` if already applied |
| GET | `/applications/{id}` | owner or scoped staff (interviewers get a limited view) |
| POST | `/applications/{id}/status` | SA, HR, RC (scoped) — `{status: under_review\|shortlisted\|on_hold\|further_review\|rejected, reason?}`; reason required for hold/reject |
| POST | `/applications/{id}/withdraw` | the candidate, if policy allows |
| POST / GET | `/applications/{id}/screening` | SA, HR, RC, HM (scoped) — `{use_ai: bool}` |
| PATCH | `/screenings/{id}` | SA, HR, RC, HM — `{matched_skills[], missing_skills[], reviewer_notes}` (override) |
| GET / POST | `/applications/{id}/decisions` | scoped; POST `{decision: recommend_hire\|recommend_reject\|approve\|reject, rationale (≥10)}` — final decisions by the job's hiring manager or HR, only after a completed interview |

### Interviews
| Method | Path | Who |
|---|---|---|
| GET | `/interviews` | scoped (interviewers: assigned only) |
| POST | `/applications/{id}/interviews` | SA, HR, RC — `{kind, scheduled_local (ISO, local), timezone (IANA), duration_minutes, location? \| meeting_link? (https), interviewer_ids[], notes?}`; application must be shortlisted; not in the past |
| GET | `/interviews/{id}` | scoped |
| POST | `/interviews/{id}/feedback` | assigned interviewer — `{ratings: {technical,problem_solving,communication,role_knowledge: 1-5}, overall_rating 1-5, recommendation, strengths?, concerns?, notes?}`; once, after start |
| POST | `/interviews/{id}/status` | `{status: completed\|cancelled, reason?}` |
| POST | `/interviews/{id}/reschedule` | SA, HR, RC — `{scheduled_local, timezone, reason?}` |
| POST | `/interviews/{id}/questions` | scoped — AI question bank. 20/min |

### Offers
| Method | Path | Who |
|---|---|---|
| GET | `/offers` | staff with `offer.view` (scoped); candidates: their own offers once extended to them |
| POST | `/applications/{id}/offers` | SA, HR — `{designation, department_id, salary > 0, currency (ISO 4217), joining_date, expires_on?, terms?}`; application must be approved for offer |
| GET | `/offers/{id}` | scoped |
| PATCH | `/offers/{id}` | SA, HR — drafts / revision requested only (bumps version) |
| POST | `/offers/{id}/submit` | SA, HR |
| POST | `/offers/{id}/decision` | SA, HR — `{decision: approved\|revision_requested, comments?}`; approval generates the PDF and extends the offer |
| POST | `/offers/{id}/withdraw` | SA, HR — `{reason}` |
| POST | `/offers/{id}/respond` | the candidate — `{response: accept\|decline, reason?}`. Accept creates exactly one employee + onboarding plan in the same transaction; repeated accepts return the same employee |

### Employees & onboarding
| Method | Path | Who |
|---|---|---|
| GET | `/employees` | SA, HR (all), HM (own team) |
| GET | `/employees/me` | EM |
| GET | `/employees/{id}` | scoped staff or the employee |
| GET | `/employees/{id}/documents` | scoped staff or the employee |
| POST | `/documents/{id}/upload` | the employee — multipart `file` |
| POST | `/documents/{id}/review` | SA, HR — `{decision: verified\|rejected, notes?}` (notes required to reject) |
| GET | `/onboarding/tasks` | HR: all; others: tasks for themselves, their reports, or assigned to them. Filters `employee_id, status` |
| POST | `/onboarding/tasks` | SA, HR, or the employee's manager — TaskIn |
| PATCH | `/onboarding/tasks/{id}` | task owner, HR or manager — `{status: pending\|in_progress\|completed\|blocked, blocked_reason?}` |
| GET / PATCH | `/training`, `/training/{id}` | scoped — `{status: assigned\|in_progress\|completed}` |
| GET | `/notifications` | own |
| POST | `/notifications/{id}/read` | own |
| GET | `/analytics/summary` | SA, HR — `?start=YYYY-MM-DD&end=YYYY-MM-DD&department=ID`; values are `null` when data is insufficient |

## Example (PowerShell)
```powershell
$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$t = (Invoke-RestMethod http://127.0.0.1:5000/api/v1/auth/csrf -WebSession $s).csrf_token
Invoke-RestMethod http://127.0.0.1:5000/api/v1/auth/login -Method Post -WebSession $s `
  -Headers @{ "X-CSRFToken" = $t } -ContentType application/json `
  -Body (@{ email = "hr@example.com"; password = "..." } | ConvertTo-Json)
Invoke-RestMethod http://127.0.0.1:5000/api/v1/jobs/clusters -WebSession $s
```
