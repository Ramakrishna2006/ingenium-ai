# Security, privacy & data handling

## Controls in the code
| Concern | Implementation |
|---|---|
| Passwords | Werkzeug `scrypt`/PBKDF2 hashes; 10+ chars, letters + digits, common-password and email-name checks (`app/security.py`). |
| Sessions | HTTP-only, `SameSite=Lax` cookies; `Secure` in production; 8 h lifetime (`SESSION_HOURS`); Flask-Login strong session protection; a `session_version` in the session id signs a user out everywhere when their role changes or they are deactivated. |
| CSRF | Flask-WTF on every form and on JSON writes (`X-CSRFToken`). |
| Rate limiting | Login, registration, uploads and AI calls (in-process; use a shared store such as Redis for multiple instances). |
| Authorization | Role → permission matrix (`app/permissions.py`) plus record-scope rules (`app/access.py`), checked in routes **and** again in services. Public sign-up is always `candidate`; there is no web route that grants Super Admin. |
| Data minimisation | Interviewers see an application without contact details; candidates/employees see only their own records; staff lists are scoped to assigned vacancies/teams. |
| Uploads | Extension allow-list, magic-byte check, size limit, empty-file check, random 32-hex stored names, stored under `UPLOAD_FOLDER` (never `frontend/static`), served only through `/files/...` with ownership checks and `Content-Disposition`/`nosniff` headers. |
| Headers | CSP (no inline scripts), `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, HSTS in production; `no-store` on API and file responses. |
| Errors | Generic error pages and JSON errors; stack traces only in logs. Production refuses to start with a weak `SECRET_KEY` or with debug on. |
| Audit | Append-only `audit_logs` for sign-ins/failures, registration, role changes, status changes, screening overrides, decisions, offers, employee creation, document reviews, settings and retention purges. No passwords or document contents are recorded. |
| Database integrity | FKs, unique constraints (one application per candidate per job; one employee per user/application/offer; one live offer per application), check constraints on every status column, transactional offer acceptance with row locking. |

## AI-specific safeguards
- AI output is advisory; no code path lets a model change a status, permission or decision.
- Resume/job text is wrapped in `<untrusted>` tags (with any closing tag in the payload neutralised) and the system prompt tells the model to ignore instructions inside it.
- Emails and phone numbers are redacted before resume text is sent; input length is capped (`AI_MAX_INPUT_CHARS`).
- Responses must be JSON matching a Pydantic schema; oversized, empty or malformed output is rejected.
- The model never receives protected characteristics; screening compares documented skills with stated job requirements only.
- Model id and analysis version are stored with each screening so results can be audited.
- AI-generated SQL or shell commands are never executed (the app never asks for any).

## Retention and deletion
- `resume_retention_days` (default 365) — `flask --app backend/run.py purge-retention` deletes resumes of candidates whose applications all closed earlier than that and who did not become employees. Run with `--dry-run` first; schedule it.
- Candidates can delete their own resumes (unless attached to an active application).
- Audit logs and hiring decisions are append-only by design; define a separate archival period with your legal team.
- Backups contain personal data: encrypt them, restrict access, and expire them in line with your policy.

## Before processing real personal data
This project is suitable for a university demonstration. Before using it with real candidates or employees:
1. **Identify the law that applies** to your organisation and candidates — e.g. India's Digital Personal Data
   Protection Act 2023 and its rules, the EU/UK GDPR for candidates in Europe, and local employment and
   anti-discrimination law. Obtain legal advice; this document is not legal advice.
2. Publish an accurate privacy notice (edit `frontend/templates/public/privacy.html`): purposes, lawful basis/consent,
   retention periods, recipients (including any AI host), and how people can access, correct or erase their data.
3. If AI runs outside your own infrastructure, assess that provider/host as a data processor and document data transfers.
4. Many jurisdictions regulate automated decision-making in hiring — keep humans as the decision-makers (as built),
   tell candidates where AI assists, and periodically review screening outcomes for adverse impact.
5. Use HTTPS only, private persistent storage for documents, managed database backups, and least-privilege staff accounts.
6. Review dependencies regularly (`pip list --outdated`, `pip-audit`) and rotate `SECRET_KEY` and database credentials if exposed.
