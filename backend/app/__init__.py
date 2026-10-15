"""Ingenium AI application factory."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    from config import BASE_DIR, FRONTEND_DIR, INSTANCE_DIR, get_config

    # The Python package lives in backend/; HTML templates and static files in frontend/.
    app = Flask(
        __name__,
        template_folder=str(FRONTEND_DIR / "templates"),
        static_folder=str(FRONTEND_DIR / "static"),
        instance_path=str(INSTANCE_DIR),
        instance_relative_config=True,
    )
    app.config.from_object(get_config(config_name))
    _validate_config(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    from app.logging_setup import configure_logging

    configure_logging(app)

    if app.config["APP_ENV"] == "production":
        # Render (and most PaaS) terminate TLS at a proxy that sets X-Forwarded-*.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db, directory=str(BASE_DIR / "migrations"), compare_type=True, render_as_batch=True)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app import models  # noqa: F401  (register models)
    from app.errors import register_error_handlers
    from app.routes import register_blueprints
    from app.security import apply_security_headers
    from app.template_helpers import register_template_helpers

    register_blueprints(app)
    register_error_handlers(app)
    register_template_helpers(app)
    app.after_request(apply_security_headers)

    from app.cli import register_cli

    register_cli(app)
    return app


def _validate_config(app: Flask) -> None:
    if app.config["APP_ENV"] != "production":
        return
    secret = app.config.get("SECRET_KEY") or ""
    if len(secret) < 32 or "change-me" in secret:
        raise RuntimeError("Production requires a strong SECRET_KEY (32+ random characters).")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("Production requires DATABASE_URL.")
    if os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"):
        raise RuntimeError("FLASK_DEBUG must be off in production.")
