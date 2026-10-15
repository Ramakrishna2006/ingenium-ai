# Ingenium AI: production image (Flask + gunicorn)
#
#   docker compose up --build      app + PostgreSQL (see docker-compose.yml)
#   docker build -t ingenium-ai .  image only
#
# Layout inside the image mirrors the repository:
#   /app/backend   Python code, migrations, CLI
#   /app/frontend  Jinja templates and static assets (served by Flask)
#   /data          private volume: uploaded resumes, employee documents, offer letters
#
# Secrets are never baked into the image: .env is excluded by .dockerignore and
# every setting is passed at runtime as an environment variable.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
# psycopg[binary], numpy, scikit-learn and reportlab all ship manylinux wheels,
# so no compiler or libpq headers are needed.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Unprivileged user; private storage lives on a volume at /data.
RUN useradd --create-home --uid 10001 ingenium \
    && mkdir -p /data/uploads \
    && chown -R ingenium:ingenium /data

COPY --chown=ingenium:ingenium backend/ backend/
COPY --chown=ingenium:ingenium frontend/ frontend/
COPY --chown=ingenium:ingenium docker/ docker/
# Strip Windows line endings in case the file was checked out with CRLF.
RUN sed -i "s/\r$//" docker/entrypoint.sh && chmod +x docker/entrypoint.sh

USER ingenium

ENV APP_ENV=production \
    FLASK_DEBUG=0 \
    PYTHONPATH=/app/backend \
    FRONTEND_DIR=/app/frontend \
    INSTANCE_DIR=/data \
    UPLOAD_FOLDER=/data/uploads \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    RUN_MIGRATIONS=1

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)" || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["sh", "-c", "exec gunicorn --chdir backend run:app --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY} --threads 4 --timeout 120 --access-logfile -"]
