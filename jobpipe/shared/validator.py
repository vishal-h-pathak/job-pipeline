"""
jobpipe.shared.validator — URL liveness check for job postings.

Moved here from jobpipe/hunt/utils/validator.py in PR-1. The legacy path is now
a re-export shim and gets removed in PR-3 when hunter consumers migrate.
"""

from __future__ import annotations

import requests

BAD_PATTERNS = ("job-not-found", "expired", "no-longer-available", "error")


def validate_url(url: str) -> bool:
    """HEAD-check a job URL. Return False only when we positively detect
    that the listing is gone. Network errors return True (don't discard)."""
    if not url:
        return True
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
    except requests.RequestException:
        return True
    if resp.status_code == 404:
        return False
    final = (resp.url or "").lower()
    if any(pat in final for pat in BAD_PATTERNS):
        return False
    return True
