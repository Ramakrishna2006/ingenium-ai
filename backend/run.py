"""Entry point. Run these from the project root (the folder that contains backend/ and frontend/).

Development:  flask --app backend/run.py run --debug      (or: python backend/run.py)
Production:   gunicorn --chdir backend run:app             (APP_ENV=production)
Docker:       docker compose up --build
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
