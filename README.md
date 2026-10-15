# Ingenium AI

An AI-assisted recruitment, employee-management and onboarding platform built with Flask, PostgreSQL,
scikit-learn and a local Ollama model. It covers the whole lifecycle: workforce planning → vacancies →
applications and resumes → screening → interviews → hiring decisions → offers → employee records →
pre-boarding, onboarding, training and probation, plus analytics.

> **AI is advisory only.** Models summarise evidence, draft text and suggest questions. Every
> shortlisting, hiring, offer and probation decision is made and recorded by an authorised person.

---

## Contents
1. [Features](#1-features)
2. [Architecture](#2-architecture)
3. [Project structure: backend and frontend](#3-project-structure-backend-and-frontend)
4. [Windows + PyCharm setup](#4-windows--pycharm-setup)
5. [Running with Docker](#5-running-with-docker)
6. [Running the tests](#6-running-the-tests)
7. [Demo walkthrough](#7-demo-walkthrough)
8. [GitHub](#8-github)
9. [Deploying to Render](#9-deploying-to-render)
10. [Operations: scheduled jobs, backups, rollback](#10-operations)
11. [Troubleshooting](#11-troubleshooting)
12. [Known limitations](#12-known-limitations)

**Just want to run it?** See [`RUN.md`](RUN.md): execution steps only, for normal (Windows) and Docker.

**Project overview, hiring flow and GitHub upload:** see [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md).

Further docs: [`docs/API.md`](docs/API.md) (REST API) · [`docs/SECURITY.md`](docs/SECURITY.md) (security, privacy, retention).

---

## 1. Features

| Area | What's implemented |
|---|---|
| **Roles** | Super Admin, HR Manager, Recruiter, Hiring Manager, Interviewer, Candidate, Employee. Permissions are a code-reviewed matrix (`backend/app/permissions.py`); record-level ownership rules live in `backend/app/access.py`. Public sign-up always creates a Candidate. |
| **Workforce planning** | Hiring requests with headcount, budget and justification; approve / reject / request revision; create vacancies from approved requests. |
| **Vacancies** | Create, edit, publish, pause, close, archive; skills, qualifications, experience, location, employment type, deadline; AI-drafted descriptions. |
| **Candidates & resumes** | Registration, profile, PDF/DOCX upload with extension + magic-byte + size checks, randomised private filenames, text extraction (optional OCR), skill/education/certification extraction, candidate and recruiter corrections. |
| **Applications** | Search/filter jobs, apply, duplicate prevention (DB unique constraint), validated status transitions, full history timeline, withdrawal (policy-controlled), notifications. |
| **Screening** | Explainable rule-based skill evidence (matched / claimed-only / not evidenced, with resume snippets), TF-IDF text similarity, optional AI summary + verification questions with model/version recorded; reviewer overrides. |
| **Clustering & matching** | TF-IDF + K-Means over published jobs with silhouette-based k selection, reproducible seed, keyword labels, persisted runs, staleness detection and edge-case fallbacks; similar-job recommendations; candidate-to-multiple-job comparison. Cluster membership, text similarity, skill overlap and decisions are kept separate. |
| **Interviews** | Scheduling with explicit IANA time zones, panels, meeting links/locations, AI question banks, independent structured scorecards, reschedule/cancel/complete, AI feedback summaries. |
| **Decisions & offers** | Recommendations and final decisions with rationale; offers with salary/currency/terms/expiry, versioned approvals, PDF letters stored privately, accept/decline. |
| **Employees & onboarding** | Acceptance + employee creation in one locked transaction (idempotent), unique employee IDs, templates by department/role, auto-generated tasks, documents with verification, training, IT/equipment requests, probation check-ins and outcome, overdue reminders. |
| **Analytics** | Application volume, pipeline conversion, vacancies by department, interview outcomes, offer acceptance, time-to-hire, onboarding completion and overdue tasks, with date/department filters and metric definitions. Missing data shows "—", never invented numbers. |
| **Admin** | Users & roles, departments, AI diagnostics + model override, audit-log viewer, system settings. |
| **Security** | Werkzeug password hashing, CSRF, secure/HTTP-only cookies, session expiry + invalidation on role change, login rate limiting, CSP and security headers, audit log, private file serving with ownership checks, prompt-injection fencing and PII redaction for AI. |

## 2. Architecture

```
Browser ──► Flask (backend/app: app factory, blueprints)
              ├── routes/        HTML pages (renders frontend/templates) and /api/v1 JSON
              ├── permissions.py role → permission matrix      access.py  record-level scope rules
              ├── schemas.py     Pydantic validation shared by forms and API
              ├── services/      business logic (recruitment, interviews, offers, onboarding,
              │                  ai_service, job_clustering, matching, resume_parser, storage, analytics)
              ├── models/        SQLAlchemy models with FKs, unique/check constraints, indexes
              └── cli.py         create-admin, seed-demo, recompute-clusters, reminders, retention
          frontend/          Jinja templates + Bootstrap 5, CSS, JS, Chart.js, logo (served by Flask)
          PostgreSQL (Alembic migrations in backend/migrations/)   Ollama (optional, local or secured remote)
          Private file storage: UPLOAD_FOLDER (never under frontend/static)
```

Routes stay thin: they validate input, call a service, commit and redirect. Services enforce permissions
and state transitions again, so the HTML pages and the API share exactly the same rules.

## 3. Project structure: backend and frontend

```
ingenium-ai/
├── backend/                  Python: everything that runs on the server
│   ├── app/                  Flask package
│   │   ├── __init__.py       app factory (points Flask at ../frontend)
│   │   ├── routes/           pages and /api/v1 endpoints
│   │   ├── services/         business logic, AI, clustering, PDFs, storage
│   │   ├── models/           SQLAlchemy models
│   │   ├── permissions.py    role → permission matrix
│   │   ├── access.py         record-level access rules
│   │   └── cli.py            create-admin, seed-demo, reminders …
│   ├── migrations/           Alembic migration history
│   ├── tests/                pytest suite
│   ├── scripts/              smoke_check.py (post-deploy check)
│   ├── config.py             settings, read from environment / .env
│   ├── run.py                entry point (Flask CLI and gunicorn)
│   └── requirements.txt
├── frontend/                 everything the browser receives
│   ├── templates/            Jinja HTML pages (public, auth, portal, staff, admin …)
│   └── static/               css/  js/  images/ (logo.svg, favicon)
├── docker/entrypoint.sh      container start-up: wait for DB, migrate, start
├── Dockerfile                production image
├── docker-compose.yml        app + PostgreSQL on your computer
├── docs/                     API.md, SECURITY.md
├── instance/                 local private data: uploads, SQLite (created automatically, git-ignored)
├── .env                      your settings (git-ignored; copy from .env.example)
├── pytest.ini                lets you run `pytest` from the project root
└── render.yaml               Render Blueprint
```

The two halves are still **one Flask application**: the backend renders the HTML templates and serves the
static files from `frontend/`, so there is no separate frontend server, build step or Node.js. Front-end
work (page layout, styles, scripts, logo) happens in `frontend/`; Python work happens in `backend/`.

**Run every command from the project root** (the folder that contains `backend/` and `frontend/`), with
`--app backend/run.py`. `.env` stays in the project root.

| Task | Command |
|---|---|
| Start the app | `flask --app backend/run.py run --debug` |
| Apply migrations | `flask --app backend/run.py db upgrade` |
| Any CLI command | `flask --app backend/run.py <command>` |
| Tests | `pytest` |
| Install packages | `pip install -r backend/requirements.txt` |
| Docker | `docker compose up --build -d` |

## 4. Windows + PyCharm setup

Tested with **Python 3.12** and 3.13, and on Windows with **Python 3.14** (full test suite passing).
Use PowerShell from the project folder.

### 4.1 Python and the virtual environment
```powershell
py --list                      # see which Python versions are installed
py -3.12 -m venv .venv         # or the version you have, e.g. py -3.14 -m venv .venv
# If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
```
`gunicorn` is skipped automatically on Windows (it is Linux-only); locally you run the Flask server.

**PyCharm:** *File → Settings → Project → Python Interpreter → Add Interpreter → Add Local Interpreter →
Virtualenv → Existing* → select `.venv\Scripts\python.exe`. Right-click the `backend` folder →
*Mark Directory as → Sources Root* (so imports like `from app import …` resolve), and right-click
`frontend/templates` → *Mark Directory as → Template Folder*. For a run configuration choose *Flask server*,
target `backend\run.py`, working directory = the project root, and tick *FLASK_DEBUG*.

### 4.2 PostgreSQL
Install PostgreSQL (16 or 17) from postgresql.org, or with winget (remember the `postgres` password you choose;
skip Stack Builder at the end, nothing from it is needed):
```powershell
winget install -e --id PostgreSQL.PostgreSQL.17 --interactive
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres     # use \16\ for version 16
```
Type the SQL below at the `postgres=#` prompt (not directly in PowerShell):
```sql
CREATE USER ingenium WITH PASSWORD 'choose-a-strong-password' CREATEDB;
CREATE DATABASE ingenium_db OWNER ingenium;
CREATE DATABASE ingenium_test OWNER ingenium;
\q
```

### 4.3 Environment variables
```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY
notepad .env                                                    # set DATABASE_URL / TEST_DATABASE_URL passwords
```
`.env` lives in the project root and is git-ignored. `UPLOAD_FOLDER` can stay blank (defaults to
`instance\uploads` in the project root, created automatically).

Edit `.env` with Notepad rather than `Add-Content`: if the file does not end with a new line, `Add-Content`
glues the new setting onto the last line (e.g. `LOG_LEVEL=INFOPOSTGRES_PASSWORD=…`). Check with
`Get-Content .env -Tail 3`.

### 4.4 Ollama (optional — everything except AI suggestions works without it)
```powershell
ollama --version              # install from https://ollama.com/download if missing
ollama pull qwen2.5:7b        # ~4.7 GB; or another model, e.g. a DeepSeek variant you have installed
ollama list
curl.exe http://127.0.0.1:11434/api/tags
```
To use a different model set `OLLAMA_MODEL` in `.env`, or change it live in *Admin → AI service*.

### 4.5 Database schema
The repository already contains the migration history, so **do not run `flask db init`**. Apply it:
```powershell
flask --app backend/run.py db upgrade
```
After changing models later: `flask --app backend/run.py db migrate -m "describe change"`, review the file in
`backend/migrations/versions/`, then `flask --app backend/run.py db upgrade`.

### 4.6 First administrator (secure bootstrap)
There is no default password and no web endpoint that grants admin rights. Create the first Super Admin
from the command line; the password is prompted twice and hidden:
```powershell
flask --app backend/run.py create-admin --email you@yourcompany.com --name "Your Name"
```
(For automation set `$env:INGENIUM_ADMIN_PASSWORD` first; remove it afterwards.)
Passwords need 10+ characters with letters and numbers, not a common password, and not your email name.

### 4.7 Optional demo data (refuses to run when `APP_ENV=production`)
```powershell
flask --app backend/run.py seed-demo --with-users     # 5 departments, 14 vacancies, 2 onboarding templates, 4 staff logins (printed once)
flask --app backend/run.py recompute-clusters
```

### 4.8 Run
```powershell
flask --app backend/run.py run --debug          # or: python backend/run.py
```
Open http://127.0.0.1:5000. Health: `/health` (liveness) and `/ready` (database check).

## 5. Running with Docker

Docker runs the app and its own PostgreSQL in containers, in **production mode** (gunicorn, no debug).
It is a second way to run the app; it does not replace or touch the PostgreSQL installed on Windows, so the
container database starts empty (see *Copy your existing data into Docker* below to bring your data across).

**1. Install Docker Desktop.** In PowerShell **run as administrator**:
```powershell
wsl --install                                  # enables WSL 2 and Virtual Machine Platform
winget install -e --id Docker.DockerDesktop
```
**Restart Windows** (required: the features above only take effect after a reboot, and PowerShell windows
opened before the install do not know the `docker` command). Then open *Docker Desktop* from the Start menu,
accept the terms (signing in is optional) and wait for **Engine running** in the bottom-left corner. In a
**new** PowerShell window:
```powershell
docker --version
docker compose version
```

**2. Add two lines to `.env`** (project root, using `notepad .env`). `SECRET_KEY` must be 32+ random characters, or the app refuses to
start in production mode:
```
POSTGRES_PASSWORD=choose-a-password-for-the-container-database
SECRET_KEY=<output of: python -c "import secrets; print(secrets.token_urlsafe(48))">
```

**3. Build and start**
```powershell
docker compose up --build -d          # first build downloads Python and installs packages (a few minutes)
docker compose ps                     # web should become "healthy"
docker compose logs -f web            # Ctrl+C stops following the log, not the app
```
On start-up the container waits for PostgreSQL and applies the migrations automatically.

**4. Create the first admin and (optionally) demo data inside the container**
```powershell
docker compose exec web flask --app backend/run.py create-admin --email you@example.com --name "Ramakrishna"
```
`seed-demo` refuses to run in production mode; to load demo vacancies into the container once, run:
```powershell
docker compose exec -e APP_ENV=development web flask --app backend/run.py seed-demo --with-users
docker compose exec web flask --app backend/run.py recompute-clusters
```

**5. Open** http://localhost:8000 (the port is bound to `127.0.0.1`, so only this computer can reach it).

**Copy your existing data into Docker (optional).** This replaces the container's database with an exact copy
of your Windows one: every login and password, vacancy, application, interview, offer and employee, plus the
uploaded resumes and documents. The Windows copy is not changed. Docker uses PostgreSQL 17, so your Windows
PostgreSQL must be 17 too (a backup from a newer version cannot be restored into an older one).
```powershell
cd <project folder>
docker compose down -v                      # removes the container database and files (not your Windows data)
docker compose up -d --wait db              # start only the container database

# Back up the Windows database with the same login the app uses (from DATABASE_URL in .env)
$url  = ((Select-String '^DATABASE_URL=' .env).Line -replace '^DATABASE_URL=', '') -replace '^postgresql\+psycopg://', 'postgresql://' -replace '^postgres://', 'postgresql://'
$dump = "$env:TEMP\ingenium_backup.dump"    # outside the project, so it never reaches git or the image
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" --dbname=$url -Fc --no-owner --no-privileges -f $dump

# Restore it into the container database
docker compose cp $dump db:/tmp/ingenium_backup.dump
docker compose exec db pg_restore -U ingenium -d ingenium_db --no-owner --no-privileges /tmp/ingenium_backup.dump
docker compose exec db rm /tmp/ingenium_backup.dump

# Start the app, then copy the uploaded files and give the app user ownership of them
docker compose up -d --wait web
docker compose cp instance/uploads/. web:/data/uploads/
docker compose exec -u root web chown -R ingenium:ingenium /data
Remove-Item $dump                           # the backup contains personal data
```
Sign in at http://localhost:8000 with the same emails and passwords as on Windows. Repeat these steps whenever you
want Docker to catch up with the Windows copy (the two do not sync automatically).

**Ollama:** the container reaches Ollama on your computer at `http://host.docker.internal:11434`. Keep Ollama
running on Windows as usual. To use another address, set `DOCKER_OLLAMA_BASE_URL` in `.env`.

**Everyday commands**
| Task | Command |
|---|---|
| Stop | `docker compose down` (data is kept in volumes) |
| Start again | `docker compose up -d` |
| Rebuild after code changes | `docker compose up --build -d` |
| Shell inside the app container | `docker compose exec web sh` |
| Database shell | `docker compose exec db psql -U ingenium -d ingenium_db` |
| Back up the container database | `docker compose exec db pg_dump -U ingenium -Fc ingenium_db > ingenium.dump` |
| **Delete everything, including data** | `docker compose down -v` |

The image never contains `.env`, `instance/`, uploads or tests (see `.dockerignore`). Uploaded files live in the
`appdata` volume and the database in the `pgdata` volume.

## 6. Running the tests
Run from the project root:
```powershell
pytest
```
If `.env` contains `TEST_DATABASE_URL`, the suite runs against that PostgreSQL test database (never your
real one); otherwise it uses a fast in-memory SQLite database. Expected: `67 passed, 1 skipped` on PostgreSQL
(the migration check uses its own throwaway SQLite database, so it is skipped there) or `68 passed` on SQLite.

68 tests (in `backend/tests/`) cover: startup and health; registration, login, password rules, CSRF, rate limiting, session
invalidation; the role permission matrix and candidate data isolation; every page rendering for every role;
the complete hiring workflow (department → jobs → application → duplicate prevention → screening → clusters →
interview → feedback → decision → offer → acceptance → exactly one employee → onboarding → metrics);
application state transitions; resume validation; AI offline / missing model / malformed or oversized output /
prompt-injection fencing / PII redaction; clustering with 0, 1, 2, duplicate, empty and many jobs; API
validation and error format; production-config and seed safeguards; and migrations (upgrade, drift check,
downgrade). The fixture refuses database URLs that look like production.

## 7. Demo walkthrough
1. Sign in as the Super Admin → *Departments* (already seeded, or add one) → *Users & roles* to add staff.
2. As HR/recruiter: *Vacancies → New vacancy* (try *Draft with AI*), publish; *Job clusters → Recompute*.
3. In a private window: *Create account* → upload a resume → apply to a job.
4. Recruiter: *Pipeline* → open the application → *Run with AI* screening → shortlist → *Schedule*.
5. Interviewer: *Interviews* → open → submit the scorecard (after the start time) → mark completed.
6. Hiring manager: application → *Approve for offer (final)*.
7. HR: *Offers* → create → approve (PDF generated).
8. Candidate: dashboard → offer → *Accept*. They become an Employee with an onboarding checklist.
9. Employee: *My onboarding* → upload documents, complete tasks. HR verifies documents on the employee profile.
10. *Analytics* updates; *Audit log* shows every sensitive step. Stop Ollama to see the app keep working without AI.

## 8. GitHub
```powershell
git init
git add .
git status            # confirm .env, .venv/, instance/ and uploads are NOT listed
git commit -m "Ingenium AI"
git branch -M main
git remote add origin https://github.com/<you>/ingenium-ai.git
git push -u origin main
```

## 9. Deploying to Render
**Option A — Blueprint (recommended).** Render → *New → Blueprint* → pick the repo. `render.yaml` creates a
PostgreSQL database and a web service with: build `pip install -r backend/requirements.txt`, pre-deploy
`flask --app backend/run.py db upgrade` (runs once per deploy, before the new code serves traffic), start
`gunicorn --chdir backend run:app --workers 2 --threads 4 --timeout 120`, health check `/health`, a generated `SECRET_KEY`,
secure cookies and a 1 GB private disk at `/var/data` for uploads.

**Option B — Free plan.** Pre-deploy commands and disks are paid features on Render. On the free plan:
create the PostgreSQL database and a Python web service manually (build `pip install -r backend/requirements.txt`,
start `gunicorn --chdir backend run:app --timeout 120`), set the environment variables below, and run migrations from your PC
against the database's **External Database URL** before each deploy that changes the schema:
```powershell
$env:DATABASE_URL="<External Database URL from Render>"
flask --app backend/run.py db upgrade
flask --app backend/run.py create-admin --email you@yourcompany.com --name "Your Name"
Remove-Item Env:DATABASE_URL
```
Without a disk, uploaded files are lost on every restart — fine for a demo, not for real documents; use a paid
disk or private object storage for production.

**Option C — Docker on Render.** *New → Web Service* → pick the repo → Language **Docker**. Render builds the
`Dockerfile`; the container applies migrations on start. Set `DATABASE_URL`, `SECRET_KEY`,
`SESSION_COOKIE_SECURE=1` and the AI variables below. (With several instances, set `RUN_MIGRATIONS=0` and run
migrations once by hand instead.)

**Environment variables:** `APP_ENV=production`, `SECRET_KEY` (32+ random chars — the app refuses to start
otherwise), `DATABASE_URL` (Render's `postgres://…` is converted to the psycopg driver automatically),
`SESSION_COOKIE_SECURE=1`, `UPLOAD_FOLDER`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_API_KEY`, `AI_ENABLED`.

**AI on Render.** `http://127.0.0.1:11434` on Render is the Render container, not your PC. Two strategies:
1. **Local AI for development and demos** — run the app and Ollama on your own machine; deploy Render with
   `AI_ENABLED=0` (or leave Ollama unreachable). The AI status pill shows *offline* and all non-AI workflows work.
2. **Separately hosted Ollama** — run Ollama on a GPU VM behind an HTTPS reverse proxy that requires a bearer
   token (e.g. Caddy/Nginx checking `Authorization: Bearer …`), firewall port 11434 from the internet, then set
   `OLLAMA_BASE_URL=https://ollama.yourdomain.com` and `OLLAMA_API_KEY=<token>`. Never expose an
   unauthenticated Ollama port publicly.

**After deploying:** open `https://<service>.onrender.com/health` and `/ready`, sign in, and run the demo
walkthrough (§7) once. Optional: `python backend/scripts/smoke_check.py https://<service>.onrender.com`.

## 10. Operations
Scheduled jobs (Render Cron Jobs, Windows Task Scheduler or cron), each a one-off CLI command:
```
flask --app backend/run.py send-reminders        # daily: notify owners of overdue onboarding tasks
flask --app backend/run.py expire-offers         # daily: expire offers past their expiry date
flask --app backend/run.py recompute-clusters    # after bulk vacancy changes (the UI flags stale clusters)
flask --app backend/run.py purge-retention --dry-run   # review, then run without --dry-run per your retention policy
flask --app backend/run.py check-ai              # diagnose Ollama connectivity
```
**Backups:** use a paid Render PostgreSQL plan for automatic daily backups and point-in-time recovery, or
`pg_dump --format=custom "$DATABASE_URL" > ingenium-YYYYMMDD.dump`; restore with
`pg_restore --clean --no-owner -d "$DATABASE_URL" ingenium-YYYYMMDD.dump`. Back up the uploads disk too.
Test a restore before you need one.

**Rollback:** in Render, *Events → Rollback* to the previous deploy. If that deploy included a migration,
first take a backup, then `flask --app backend/run.py db downgrade -1` from a machine with database access, then roll
back the code. Prefer additive (backwards-compatible) migrations so code can roll back without schema changes.

**Logging:** JSON logs in production (`LOG_JSON=1`) with a request ID on each line; resume text, passwords and
document contents are never logged.

## 11. Troubleshooting
| Symptom | Fix |
|---|---|
| `py -3.12` not found / wrong interpreter | Use the version you have (`py --list`, e.g. `py -3.14`); in PyCharm select `.venv\Scripts\python.exe`; check with `python -c "import sys; print(sys.executable)"`. |
| `No module named flask` / missing packages | Activate the venv (`.\.venv\Scripts\Activate.ps1`) then `pip install -r backend/requirements.txt`. |
| `pip` missing | `python -m ensurepip --upgrade`. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| `connection refused` on port 5432 | Start the *postgresql-x64-17* (or -16) service (`services.msc`); check host/port in `DATABASE_URL`. |
| Settings ignored / odd errors after editing `.env` | Two settings ended up on one line; run `Get-Content .env -Tail 5` and put each `KEY=value` on its own line. |
| `password authentication failed` / invalid URL | Recheck the user/password; URL-encode special characters (`@` → `%40`). Format: `postgresql://user:pass@localhost:5432/ingenium_db`. |
| `relation "users" does not exist` | Migrations not applied: `flask --app backend/run.py db upgrade`. |
| Migration fails | Read the error, fix the DB state, retry; `flask --app backend/run.py db current` shows the applied revision. Never edit an already-applied migration. |
| AI pill says *offline* | Start Ollama; `curl.exe http://127.0.0.1:11434/api/tags`; check `OLLAMA_BASE_URL`. |
| AI pill says *model missing* | `ollama pull qwen2.5:7b` (or the configured model). |
| `Could not locate a Flask application` / `No module named app` | Run commands from the project root with `--app backend/run.py`; ensure the venv is active. In PyCharm mark `backend` as Sources Root. |
| `TemplateNotFound` or pages without styling | The `frontend/` folder is missing or was not copied next to `backend/`. |
| `docker` is not recognized | Docker Desktop was just installed: restart Windows, start Docker Desktop, and use a new PowerShell window. |
| Docker Desktop: *Virtual Machine Platform is not enabled* | Restart Windows after `wsl --install`. If it persists, in admin PowerShell run `Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All` and `wsl --update`, then restart. Also check *Task Manager → Performance → CPU → Virtualization: Enabled*; if disabled, turn on Intel VT-x / AMD SVM in the BIOS. |
| Docker: `required variable … is missing a value` | Add `POSTGRES_PASSWORD` and `SECRET_KEY` to `.env` in the project root. |
| Docker: `web` keeps restarting | `docker compose logs web`; usually a weak `SECRET_KEY` or the database password changed after the first start (run `docker compose down -v` to reset the container database). |
| Docker: `pg_restore: unsupported version` or `server version mismatch` | The container and Windows PostgreSQL major versions differ; keep both on 17 (`image: postgres:17-alpine`). After changing the image run `docker compose down -v` first. |
| Docker: AI pill *offline* | Ollama must be running on the host; check `DOCKER_OLLAMA_BASE_URL`. |
| Upload directory errors | It is created on start-up; ensure `UPLOAD_FOLDER` points to a writable folder. |
| Port 5000 already in use | `flask --app backend/run.py run --port 5050`, or find it: `netstat -ano | findstr :5000`. |
| Production refuses to start | Set a strong `SECRET_KEY` and `DATABASE_URL`; turn off `FLASK_DEBUG`. |

## 12. Known limitations
- Rate limiting is in-process (per worker). For several instances, put a shared limiter (e.g. Redis) in front.
- Clustering runs on demand/CLI, not in a background queue; fine for hundreds of vacancies.
- Email (verification, password reset, notifications by email) needs `MAIL_SERVER`; without it, notifications are in-app only.
- OCR for scanned PDFs requires installing Tesseract + Poppler and `pip install pytesseract pdf2image`, then `ENABLE_OCR=1`.
- Analytics charts load Chart.js from jsDelivr; offline, each chart falls back to its data table.
- This is a university-grade system structured for hardening; review `docs/SECURITY.md` and local employment and
  data-protection law before processing real candidate data.
