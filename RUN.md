# Ingenium AI: How to Run

Execution steps only. There are two separate ways to run the app; use either one, or both side by side:

| | **A. Normal (Windows)** | **B. Docker** |
|---|---|---|
| Address | http://127.0.0.1:5000 | http://localhost:8000 |
| Database | PostgreSQL 17 installed on Windows | Its own PostgreSQL 17 inside Docker |
| Mode | Development (debug, auto-reload) | Production (gunicorn) |

Run every command in **PowerShell** from the project folder (the folder that contains `backend\` and `frontend\`):

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
```

---

## The `.env` file (needed by both A and B)

The project folder must contain a file named `.env`. Create or edit it with `notepad .env`. It needs **one copy only** of these lines, plus the other lines from `.env.example`:

```
SECRET_KEY=<a long random string, 32+ characters>
APP_ENV=development
FLASK_DEBUG=1
DATABASE_URL=postgresql://ingenium:HireEmp@localhost:5432/ingenium_db
TEST_DATABASE_URL=postgresql://ingenium:HireEmp@localhost:5432/ingenium_test
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:7b
POSTGRES_PASSWORD=HireEmp
```

Generate a `SECRET_KEY` with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Check the file:

```powershell
Get-Content .env | Select-String "SECRET_KEY|DATABASE_URL|POSTGRES_PASSWORD"
```

> Do not run `Copy-Item .env.example .env` once `.env` exists. It overwrites your settings with the blank template.

---

# A. Normal execution (Windows)

### A1. First time only

```powershell
# 1. Virtual environment (use the Python version you have: py --list)
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r backend\requirements.txt

# 2. Database tables
flask --app backend/run.py db upgrade

# 3. First administrator (password is asked twice, hidden)
flask --app backend/run.py create-admin --email you@example.com --name "Ramakrishna"

# 4. Optional demo data: departments, vacancies, 4 staff logins (passwords are printed once, save them)
flask --app backend/run.py seed-demo --with-users
flask --app backend/run.py recompute-clusters
```

If `Activate.ps1` is blocked, run this once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

> If you move or copy the project folder, delete `.venv` and redo step 1. A virtual environment only works in the folder where it was created.

### A2. Every time you run the app

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
.\.venv\Scripts\Activate.ps1
flask --app backend/run.py run --debug
```

Open **http://127.0.0.1:5000**. Press **Ctrl+C** to stop.

Optional, for the AI features: keep **Ollama** running (it usually starts with Windows). Check it with:

```powershell
ollama list
```

### A3. Run the tests

```powershell
pytest
```

Expected: `67 passed, 1 skipped`.

### A4. After downloading a new version of the code

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
flask --app backend/run.py db upgrade
```

### A5. Useful commands

| Task | Command |
|---|---|
| Health check | open http://127.0.0.1:5000/health |
| Check the AI connection | `flask --app backend/run.py check-ai` |
| Re-run job clustering | `flask --app backend/run.py recompute-clusters` |
| Send overdue onboarding reminders | `flask --app backend/run.py send-reminders` |
| Expire old offers | `flask --app backend/run.py expire-offers` |
| Use another port | `flask --app backend/run.py run --debug --port 5050` |

---

# B. Docker execution

Docker runs the app and its **own PostgreSQL 17** in two containers (`web` and `db`). It does not use or change the PostgreSQL installed on Windows. Docker data is kept in Docker volumes until you delete it with `docker compose down -v`.

### B1. Install Docker Desktop (once per computer)

Skip this section if `docker --version` already prints a version.

1. In **PowerShell as administrator** (right-click PowerShell, then **Run as administrator**):

   ```powershell
   wsl --install --no-distribution
   winget install -e --id Docker.DockerDesktop
   ```

   - `--no-distribution` turns on WSL without downloading Ubuntu. Docker does not need Ubuntu.
   - If winget says *"Found an existing package already installed... No available upgrade found"*, Docker Desktop is already installed. That is fine.

2. **Restart Windows.** This is required: WSL and Docker do not work until after a restart.
3. Open **Docker Desktop** from the Start menu. Accept the terms (signing in is optional) and wait until the bottom-left corner shows **Engine running**.
4. Open a **new** PowerShell window and check:

   ```powershell
   docker --version
   docker compose version
   ```

### B2. Check `.env` before the first start

Docker reads the same `.env` file as the normal run. It needs these two lines:

```
SECRET_KEY=<the same long random string as above, 32+ characters>
POSTGRES_PASSWORD=HireEmp
```

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
Get-Content .env | Select-String "SECRET_KEY|POSTGRES_PASSWORD"
```

Each must appear **once**, each on its own line. You do not need to change `DATABASE_URL` or `APP_ENV`: inside Docker they are replaced automatically (database = the `db` container, mode = production).

### B3. First start: build the containers

Docker Desktop must show **Engine running**.

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai
docker compose up --build -d
```

The first build downloads Python and PostgreSQL and installs the packages, which takes a few minutes. Then check:

```powershell
docker compose ps
```

Wait until **db** shows `(healthy)` and **web** shows `(healthy)`. Web first shows `(health: starting)` for about 20 to 40 seconds while it waits for the database and creates the tables. Run `docker compose ps` again until it is healthy.

### B4. Put data into Docker (choose ONE)

The Docker database starts **empty**. Choose option 1 **or** option 2, not both.

#### Option 1: start fresh

```powershell
# Administrator (password is asked twice, hidden)
docker compose exec web flask --app backend/run.py create-admin --email you@example.com --name "Ramakrishna"

# Optional demo data: departments, 14 vacancies, 4 staff logins (passwords are printed once, save them)
docker compose exec -e APP_ENV=development web flask --app backend/run.py seed-demo --with-users
docker compose exec web flask --app backend/run.py recompute-clusters
```

The demo staff passwords printed here are **new**; they are different from your Windows ones.

#### Option 2: copy everything from your Windows app

This copies all users **with the same passwords** (Ramakrishna, Chandu, Susmita, Amar, Sai, Anjali, Kiran...), jobs, applications, interviews, offers, employees, onboarding and uploaded files. It **replaces** everything in Docker. Your Windows data is only read, not changed.

Before you start:
- The Windows PostgreSQL service must be running (the normal app must be able to start).
- Paste **all** lines into the **same** PowerShell window, because `$url` and `$dump` are only remembered there.

```powershell
cd C:\Users\ramks\OneDrive\Desktop\Github_Projects\ingenium-ai

# 1. Empty the Docker database and start only the database container
docker compose down -v
docker compose up -d --wait db

# 2. Back up the Windows database (uses DATABASE_URL from .env)
$url  = ((Select-String '^DATABASE_URL=' .env).Line -replace '^DATABASE_URL=', '') -replace '^postgresql\+psycopg://', 'postgresql://'
$dump = "$env:TEMP\ingenium_backup.dump"
& "C:\Program Files\PostgreSQL\17\bin\pg_dump.exe" --dbname=$url -Fc --no-owner --no-privileges -f $dump

# 3. Restore it into the Docker database
docker compose cp $dump db:/tmp/ingenium_backup.dump
docker compose exec db pg_restore -U ingenium -d ingenium_db --no-owner --no-privileges /tmp/ingenium_backup.dump
docker compose exec db rm /tmp/ingenium_backup.dump

# 4. Start the app and copy the uploaded resumes, documents and offer letters
docker compose up -d --wait web
docker compose cp instance/uploads/. web:/data/uploads/
docker compose exec -u root web chown -R ingenium:ingenium /data

# 5. Delete the backup file (it contains personal data)
Remove-Item $dump
```

Do **not** run `create-admin` or `seed-demo` after option 2; the accounts already exist. Docker does not stay in sync with Windows: to bring later Windows changes across, run option 2 again.

### B5. Open the app

Open **http://localhost:8000** and sign in.

- Option 1: use the administrator you just created.
- Option 2: use the same emails and passwords as on Windows.

### B6. Every day

Start **Docker Desktop** and wait for **Engine running**, then:

| Task | Command |
|---|---|
| Start | `docker compose up -d` |
| Check it is running | `docker compose ps` |
| Stop (data is kept) | `docker compose down` |

All commands are run from the project folder. If Docker Desktop is closed, the app is not running.

### B7. After changing the code or `.env`

| What changed | Command |
|---|---|
| Code (new zip copied in, files edited) | `docker compose up --build -d` |
| Only `.env` | `docker compose up -d --force-recreate` |

Database migrations run automatically every time the web container starts.

### B8. Useful Docker commands

| Task | Command |
|---|---|
| Live log of the app | `docker compose logs -f web` (Ctrl+C stops the log, not the app) |
| Log of the database | `docker compose logs db` |
| Any CLI command | `docker compose exec web flask --app backend/run.py <command>` |
| Check the AI connection | `docker compose exec web flask --app backend/run.py check-ai` |
| Shell inside the app | `docker compose exec web sh` |
| Database shell | `docker compose exec db psql -U ingenium -d ingenium_db` |
| Back up the Docker database | `docker compose exec db pg_dump -U ingenium -Fc ingenium_db > ingenium_docker.dump` |
| **Delete all Docker data** (database and uploads) | `docker compose down -v` |

The AI inside Docker uses **Ollama running on Windows** (through `host.docker.internal:11434`), so keep Ollama running on Windows as usual.

---

## Quick fixes

### Normal (Windows)

| Problem | Fix |
|---|---|
| `Fatal error in launcher` | The project folder was moved. Delete `.venv` and redo A1 step 1. |
| `No suitable Python runtime found` | Use the version you have: `py --list`, then for example `py -3.14 -m venv .venv`. |
| `No module named flask` | Activate the virtual environment: `.\.venv\Scripts\Activate.ps1` |
| `password authentication failed` | `.env` has the wrong password, or two copies of the settings. Keep one copy with `HireEmp`. |
| `relation "users" does not exist` | `flask --app backend/run.py db upgrade` |
| AI shows *offline* | Start Ollama, then `ollama pull qwen2.5:7b` |

### Docker

| Problem | Fix |
|---|---|
| `docker` is not recognized | Docker Desktop was just installed: restart Windows, start Docker Desktop, open a **new** PowerShell window. |
| `wsl --install` starts downloading Ubuntu | Not needed for Docker: press Ctrl+C. Use `wsl --install --no-distribution`. To remove an installed Ubuntu: `wsl --unregister Ubuntu`. |
| Docker Desktop: *Virtual Machine Platform is not enabled* | Restart Windows after `wsl --install`. If it stays: admin PowerShell `Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All`, then `wsl --update`, then restart. Check *Task Manager > Performance > CPU > Virtualization: Enabled* (if Disabled, enable VT-x/SVM in the BIOS). |
| `error during connect` / `cannot find the file specified ... dockerDesktopLinuxEngine` | Docker Desktop is not running. Open it and wait for **Engine running**. |
| `required variable POSTGRES_PASSWORD (or SECRET_KEY) is missing a value` | Add that line to `.env` (B2), once, on its own line. |
| `web` keeps restarting / not healthy | `docker compose logs web`. Usually a weak or missing `SECRET_KEY` (needs 32+ random characters). |
| `db` keeps restarting; log says *database files are incompatible with server* | The Docker volume was created by an older PostgreSQL (16). Reset the Docker data: `docker compose down -v`, then B3 and B4 again. |
| `password authentication failed` in Docker | `POSTGRES_PASSWORD` was changed after the first start. Either change it back, or reset: `docker compose down -v`, then B3 and B4. |
| `pg_dump` (B4 option 2): `password authentication failed` or `connection refused` | The Windows database is not reachable: check `DATABASE_URL` in `.env` and that the normal app starts. |
| `pg_dump` not found | Your PostgreSQL is in another folder (for example `...\PostgreSQL\16\bin`). Use that path. |
| `pg_restore: unsupported version` / `server version mismatch` | Windows and Docker PostgreSQL major versions differ. Docker uses 17; install PostgreSQL 17 on Windows or use its `pg_dump`. |
| Port 8000 is already in use | Close the other program, or change `"127.0.0.1:8000:8000"` to `"127.0.0.1:8001:8000"` in `docker-compose.yml` and open http://localhost:8001. |
| Resumes or documents give *file not found* after option 2 | The uploads step was skipped. Run the two `docker compose cp` / `chown` lines from B4 option 2 step 4 again. |
| AI shows *offline* in Docker | Ollama must be running on Windows. Test from the container: `docker compose exec web python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').status)"` (should print 200). |
