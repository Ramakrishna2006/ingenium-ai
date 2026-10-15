"""Post-deployment smoke check.

    python backend/scripts/smoke_check.py https://ingenium-ai.onrender.com

Checks liveness, readiness (database), the public careers page, that the API rejects anonymous access,
and that security headers are present. Exits non-zero on any failure.
"""
from __future__ import annotations

import sys

import requests


def main(base: str) -> int:
    base = base.rstrip("/")
    checks = [
        ("liveness", "/health", lambda r: r.status_code == 200 and r.json().get("status") == "ok"),
        ("readiness (database)", "/ready", lambda r: r.status_code == 200 and r.json()["checks"]["database"] == "ok"),
        ("careers page", "/careers/", lambda r: r.status_code == 200 and "Ingenium AI" in r.text),
        ("API requires auth", "/api/v1/applications", lambda r: r.status_code == 401),
        ("security headers", "/", lambda r: "Content-Security-Policy" in r.headers and "X-Frame-Options" in r.headers),
    ]
    failed = 0
    for name, path, ok in checks:
        try:
            resp = requests.get(base + path, timeout=30)
            passed = ok(resp)
            detail = resp.status_code
        except Exception as exc:  # noqa: BLE001 - report any failure
            passed, detail = False, type(exc).__name__
        failed += not passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name:22} {path} ({detail})")
    if base.startswith("https://"):
        r = requests.get(base + "/ready", timeout=30)
        hsts = "Strict-Transport-Security" in r.headers
        print(f"[{'PASS' if hsts else 'WARN'}] HSTS header")
    print("All checks passed." if not failed else f"{failed} check(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
