"""jobpipe.config — process-environment helpers shared by all sub-packages.

PR-6 promotes the per-subtree ``_require`` (jobpipe/submit/config.py)
into a single canonical entry point so that every sub-package fails the
same way on missing env vars (uniform error message + .env.example
pointer).

Currently exposes only ``require_env``. Add new functions here when a
new piece of cross-subtree env-driven config emerges; do NOT promote
sub-package-specific tunables (those stay in
``jobpipe/{hunt,tailor,submit}/config.py``).
"""

from __future__ import annotations

import os


def require_env(name: str) -> str:
    """Return ``os.environ[name]`` or raise with a uniform error message.

    Used by ``jobpipe.submit.config`` for required secrets at import
    time so misconfiguration crashes the process before any polling
    starts. The error message points the operator at ``.env.example``
    so the missing key has an obvious place to look up the expected
    value/format.
    """
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Missing required env var: {name}. See .env.example."
        )
    return val
