"""Application configuration.

All secrets come from environment variables (or a local, git-ignored .env file).
Three profiles are provided: development, testing and production.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

# Project layout:
#   <project>/backend   Python code (this file lives here)
#   <project>/frontend  Jinja templates and static assets (CSS, JS, images)
#   <project>/instance  private local data: uploads and the optional SQLite file (git-ignored)
BASE_DIR = Path(__file__).resolve().parent          # backend/
PROJECT_DIR = BASE_DIR.parent                         # project root
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR") or PROJECT_DIR / "frontend")
INSTANCE_DIR = Path(os.environ.get("INSTANCE_DIR") or PROJECT_DIR / "instance")

# .env lives in the project root (backend/.env is also accepted).
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(url: str | None) -> str | None:
    """Make Render/Heroku style URLs use the psycopg (v3) SQLAlchemy driver.

    Render exposes ``postgres://`` or ``postgresql://`` URLs. SQLAlchemy 2 does
    not accept ``postgres://`` and would otherwise look for psycopg2.
    """
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class BaseConfig:
    APP_NAME = "Ingenium AI"
    APP_ENV = "base"
    SECRET_KEY = os.environ.get("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Sessions & cookies
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("SESSION_HOURS", "8")))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_DURATION = timedelta(days=7)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None  # token valid for the session lifetime

    # Uploads (private: never under frontend/static)
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or str(INSTANCE_DIR / "uploads")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024
    ENABLE_OCR = _bool("ENABLE_OCR", False)

    # Ollama
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY")  # for a secured, remote Ollama proxy
    OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "90"))
    OLLAMA_MAX_RETRIES = int(os.environ.get("OLLAMA_MAX_RETRIES", "1"))
    OLLAMA_MAX_CONCURRENCY = int(os.environ.get("OLLAMA_MAX_CONCURRENCY", "2"))
    AI_MAX_INPUT_CHARS = int(os.environ.get("AI_MAX_INPUT_CHARS", "6000"))
    AI_ENABLED = _bool("AI_ENABLED", True)

    # Email (optional). When MAIL_SERVER is empty, email features are disabled.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", True)
    MAIL_FROM = os.environ.get("MAIL_FROM", "no-reply@ingenium.local")

    # Rate limiting (in-process; see docs/SECURITY.md for multi-instance setups)
    RATELIMIT_ENABLED = _bool("RATELIMIT_ENABLED", True)
    LOGIN_RATE_LIMIT = os.environ.get("LOGIN_RATE_LIMIT", "10/300")  # attempts / seconds

    # Policy
    OFFER_REQUIRE_SEPARATE_APPROVER = _bool("OFFER_REQUIRE_SEPARATE_APPROVER", False)
    ITEMS_PER_PAGE = 15
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_JSON = _bool("LOG_JSON", False)


class DevelopmentConfig(BaseConfig):
    APP_ENV = "development"
    DEBUG = _bool("FLASK_DEBUG", True)
    SECRET_KEY = BaseConfig.SECRET_KEY or "dev-only-insecure-key-change-me"
    SQLALCHEMY_DATABASE_URI = BaseConfig.SQLALCHEMY_DATABASE_URI or (
        "sqlite:///" + str(INSTANCE_DIR / "ingenium_dev.sqlite3")
    )


class TestingConfig(BaseConfig):
    APP_ENV = "testing"
    TESTING = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.environ.get("TEST_DATABASE_URL")) or "sqlite://"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    OLLAMA_BASE_URL = "http://127.0.0.1:9"  # nothing listens here: AI is "offline" by default in tests
    OLLAMA_TIMEOUT = 2
    OLLAMA_MAX_RETRIES = 0
    MAIL_SERVER = ""


class ProductionConfig(BaseConfig):
    APP_ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", True)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    PREFERRED_URL_SCHEME = "https"
    LOG_JSON = _bool("LOG_JSON", True)


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("APP_ENV") or "development"
    if name not in CONFIGS:
        raise RuntimeError(f"Unknown APP_ENV '{name}'. Use one of: {', '.join(CONFIGS)}")
    return CONFIGS[name]
