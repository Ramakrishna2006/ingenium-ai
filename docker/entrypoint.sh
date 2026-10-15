#!/bin/sh
# Container entrypoint: wait for the database, apply migrations once, then start the app.
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Waiting for the database..."
  python - <<'PY'
import os, sys, time
import sqlalchemy as sa
from config import normalize_database_url

url = normalize_database_url(os.environ.get("DATABASE_URL"))
if not url:
    sys.exit("DATABASE_URL is not set.")
engine = sa.create_engine(url, pool_pre_ping=True)
for attempt in range(30):
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        print("Database is reachable.")
        break
    except Exception as exc:  # noqa: BLE001
        print(f"  not ready yet ({type(exc).__name__}); retrying in 2s")
        time.sleep(2)
else:
    sys.exit("Database never became reachable.")
PY
  echo "Applying database migrations..."
  flask --app backend/run.py db upgrade
fi

exec "$@"
