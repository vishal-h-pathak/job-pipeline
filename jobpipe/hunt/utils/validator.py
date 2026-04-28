"""utils/validator.py — PR-1 re-export shim.

Canonical implementation lives at jobpipe.shared.validator. Removed in PR-3
once consumers migrate.
"""

from jobpipe.shared.validator import (  # noqa: F401  PR-1 re-export
    BAD_PATTERNS,
    validate_url,
)
