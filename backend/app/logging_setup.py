"""Structured logging with a per-request id (X-Request-ID)."""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid

from flask import g, has_request_context, request


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(g, "request_id", "-") if has_request_context() else "-"
        record.path = request.path if has_request_context() else "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "path": getattr(record, "path", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(app) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    if app.config.get("LOG_JSON"):
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(app.config.get("LOG_LEVEL", "INFO"))
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    access_log = logging.getLogger("ingenium.access")

    @app.before_request
    def _start():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming[:64] if incoming.isalnum() else uuid.uuid4().hex[:16]
        g.started = time.perf_counter()

    @app.after_request
    def _finish(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "-")
        if not request.path.startswith("/static"):
            elapsed = (time.perf_counter() - getattr(g, "started", time.perf_counter())) * 1000
            access_log.info("%s %s %s %.0fms", request.method, request.path, response.status_code, elapsed)
        return response
