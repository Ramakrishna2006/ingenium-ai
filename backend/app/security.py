"""Password policy, safe redirects, rate limiting and security headers."""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import abort, current_app, request

from app.errors import ValidationFailed

PASSWORD_MIN_LENGTH = 10
COMMON_PASSWORDS = {"password123", "qwerty12345", "1234567890", "letmein1234", "ingenium1", "admin12345"}


def password_problems(password: str, email: str | None = None) -> list[str]:
    problems = []
    if len(password or "") < PASSWORD_MIN_LENGTH:
        problems.append(f"at least {PASSWORD_MIN_LENGTH} characters")
    if len(password or "") > 128:
        problems.append("at most 128 characters")
    if not re.search(r"[A-Za-z]", password or ""):
        problems.append("at least one letter")
    if not re.search(r"\d", password or ""):
        problems.append("at least one number")
    if (password or "").lower() in COMMON_PASSWORDS:
        problems.append("not a commonly used password")
    if email and password and email.split("@")[0].lower() in password.lower() and len(email.split("@")[0]) >= 4:
        problems.append("not contain your email name")
    return problems


def validate_password(password: str, email: str | None = None) -> None:
    problems = password_problems(password, email)
    if problems:
        raise ValidationFailed("Password must have " + ", ".join(problems) + ".", details={"password": problems})


def is_safe_redirect(target: str | None) -> bool:
    if not target:
        return False
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in ("http", "https") and host_url.netloc == redirect_url.netloc


class InMemoryRateLimiter:
    """Sliding-window limiter, per process.

    Adequate for a single Gunicorn instance/demo. For several instances use a shared
    store (e.g. Flask-Limiter with Redis) — see docs/SECURITY.md.
    """

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = InMemoryRateLimiter()


def _parse(spec: str) -> tuple[int, int]:
    count, _, seconds = spec.partition("/")
    return int(count), int(seconds or 60)


def rate_limit(spec: str | None = None, config_key: str | None = None, methods=("POST",)):
    """Decorator: limit requests per client IP + endpoint. ``spec`` is 'count/seconds'."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_app.config.get("RATELIMIT_ENABLED") and request.method in methods:
                limit, window = _parse(current_app.config.get(config_key) if config_key else spec)
                client = request.access_route[0] if request.access_route else request.remote_addr
                if not limiter.hit(f"{request.endpoint}:{client}", limit, window):
                    abort(429)
            return view(*args, **kwargs)

        return wrapped

    return decorator


CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com data:; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", CSP)
    if current_app.config.get("SESSION_COOKIE_SECURE"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith(("/api/", "/files/")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response
