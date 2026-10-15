# Ingenium AI: Project Guide

How to run the project, how it works, and how to upload it to GitHub.

Contents:

1. [Running the project](#1-running-the-project)
2. [How the project flows](#2-how-the-project-flows)
3. [Uploading to GitHub](#3-uploading-to-github)

All commands are for **Windows PowerShell**, run from the project folder (the folder that contains `backend\` and `frontend\`). Full run details and fixes for common problems are in [`RUN.md`](RUN.md).

---

## 1. Running the project

There are two separate ways to run the app. Use either one, or both side by side.

| | A. Normal (Windows) | B. Docker |
|---|---|---|
| Address | http://127.0.0.1:5000 | http://localhost:8000 |
| Database | PostgreSQL 17 on Windows | Its own PostgreSQL 17 inside Docker |
| Mode | Development (debug) | Production (gunicorn) |

### A. Normal (Windows)

**Every day:**

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
.\.venv\Scripts\Activate.ps1
flask --app backend/run.py run --debug
```

Open http://127.0.0.1:5000. Press **Ctrl+C** to stop. Keep **Ollama** running for the AI features.

**After changing or downloading code:**

```powershell
pip install -r backend\requirements.txt
flask --app backend/run.py db upgrade
pytest                     # expect: 67 passed, 1 skipped
```

**First time on a new computer** (see `RUN.md`, section A1):

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
# create .env (one copy of the settings; see RUN.md)
flask --app backend/run.py db upgrade
flask --app backend/run.py create-admin --email you@example.com --name "Ramakrishna"
```

### B. Docker

Open **Docker Desktop** and wait for **Engine running**, then:

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
docker compose up -d            # start
docker compose ps               # web and db should show (healthy)
docker compose down             # stop (data is kept)
docker compose up --build -d    # after code changes
```

First-time Docker setup, and copying your Windows data into Docker, are in `RUN.md`, sections B1 to B4.

---

## 2. How the project flows

### 2.1 How one request moves through the app

```
Browser (frontend/templates + frontend/static)
   -> Flask route (backend/app/routes)                receives the click or form
   -> permission check (permissions.py, access.py)    is this role allowed?
   -> validation (schemas.py, Pydantic)               is the input correct?
   -> service (backend/app/services)                  business rules, AI, ML, PDFs
   -> models (SQLAlchemy) -> PostgreSQL               save or read data
   -> page rendered back to the browser
```

### 2.2 The people and their roles

| Person | Role | What they do |
|---|---|---|
| Ramakrishna | Super Admin | Users and roles, departments, AI settings, audit log, settings |
| Chandu | HR Manager | Approves hiring requests, vacancies, offers, employees, onboarding, documents |
| Susmita | Recruiter | Vacancies, candidate pipeline, screening, scheduling interviews |
| Amar | Hiring Manager | Hiring requests, final hiring decisions, probation reviews |
| Sai | Interviewer | Only the interviews assigned to him; opens the resume, submits scorecards |
| Anjali, Kiran, Meera, Rahul | Candidate | Create an account, upload a resume, apply, accept or decline offers |
| A hired candidate | Employee | Own onboarding: tasks, document uploads, IT requests |

Public sign-up always creates a **Candidate**. Staff roles are only given by the Super Admin.

### 2.3 The hiring flow, using our people

| # | Who | Step |
|---|---|---|
| 1 | Amar (Hiring Manager) | Raises a hiring request |
| 2 | Chandu (HR) | Approves it, creates the vacancy (AI can draft it), publishes it |
| 3 | Job seeker (Anjali, Meera...) | Apply for a job -> Create account -> uploads resume -> Apply |
| 4 | System | Reads the resume and extracts skills; clustering groups similar jobs |
| 5 | Susmita (Recruiter) | Screens the application (skills match + AI summary) -> Shortlist -> Schedule interview |
| 6 | Sai (Interviewer) | Opens the resume -> submits the scorecard -> marks the interview complete |
| 7 | Amar | Makes the final decision: Approve for offer |
| 8 | Chandu | Creates and approves the offer; the PDF offer letter is generated |
| 9 | Job seeker | Accepts, and becomes an Employee (code IN2026...) |
| 10 | Employee | Onboarding: uploads ID, signed offer and certificates; sends IT requests |
| 11 | Chandu | Verifies the documents, handles IT requests and tasks |
| 12 | Amar | Probation check-ins and outcome |
| 13 | Ramakrishna (Admin) | Users, departments, AI settings, audit log; analytics for everyone allowed |

### 2.4 Where the AI fits

The local AI model (Ollama running `qwen2.5:7b`) only **suggests**:

- drafts job descriptions,
- summarises resumes and lists questions to verify,
- suggests interview questions and summarises scorecards,
- suggests onboarding tasks.

**Every decision is made and recorded by a person.** If Ollama is not running, the app shows "AI offline" and every other feature keeps working.

### 2.5 Machine learning

- **Job clustering:** TF-IDF + K-Means (scikit-learn) groups similar vacancies; the number of groups is chosen by silhouette score.
- **Matching:** a candidate's resume is compared with jobs by skill overlap and text similarity (cosine similarity), shown separately so recruiters can see why.

### 2.6 Security in short

- Email + password sign-in with server-side sessions (Flask-Login); passwords hashed with scrypt.
- Role permissions on every page, plus record-level rules (for example, an interviewer sees only resumes from interviews assigned to them).
- CSRF tokens on every form, login rate limiting, security headers, audit log.
- Uploaded files are stored privately and served only after permission checks.

---

## 3. Uploading to GitHub

### Step 1: one-time setup

```powershell
git --version                       # if not found: winget install -e --id Git.Git, then open a new PowerShell
git config --global user.name  "Ramakrishna"
git config --global user.email "you@example.com"     # the email of your GitHub account
```

### Step 2: create an empty repository on GitHub

1. Sign in at **github.com**, click **+** (top right), then **New repository**.
2. Name: `ingenium-ai`. Choose **Private** (recommended) or **Public**.
3. Do **not** tick "Add a README", ".gitignore" or "license". The project already has them.
4. Click **Create repository** and keep the page open.

### Step 3: check that secrets will not be uploaded

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
git init
git check-ignore .env .venv instance
```

All three names must be printed back. That means Git ignores them. If one is missing, **stop and fix `.gitignore` first**:

- `.env` contains your passwords and `SECRET_KEY`.
- `instance\` contains uploaded resumes and ID documents.
- `.venv\` is your local Python environment (large, and specific to your computer).

### Step 4: commit

```powershell
git add .
git status
```

Read the list. It should show `backend/`, `frontend/`, `docker/`, `docs/`, `README.md`, `RUN.md`, `PROJECT_GUIDE.md`, `Dockerfile`, `docker-compose.yml` and similar. It must **not** show `.env`, `.venv/`, `instance/`, or any `.dump` or `.zip` file.

```powershell
git commit -m "Ingenium AI: recruitment, onboarding and employee management"
git branch -M main
```

### Step 5: push

Replace `<your-username>` with your GitHub username:

```powershell
git remote add origin https://github.com/<your-username>/ingenium-ai.git
git push -u origin main
```

The first time, a browser window opens asking you to sign in to GitHub and approve. Refresh the repository page: your files appear, with `README.md` shown on the front page.

### Later: uploading new changes

```powershell
git add .
git status                 # check again: no .env, .venv or instance files
git commit -m "Describe what you changed"
git push
```

### If a secret is pushed by mistake

Deleting the file in a new commit is **not** enough, because it stays in the history. Change that password or `SECRET_KEY` immediately, then remove the file from the history before pushing again.

### Never put in the repository

- `.env` (passwords, `SECRET_KEY`)
- `instance/` (uploaded resumes and documents)
- database backups (`*.dump`)
- demo staff passwords printed by `seed-demo` (keep them in a private note)
