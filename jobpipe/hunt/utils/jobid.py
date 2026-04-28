"""utils/jobid.py — PR-1 re-export shim.

The canonical implementation moved to jobpipe.shared.jobid. This file
re-exports for backward compatibility with pre-PR-3 hunter source modules
that import via ``from utils.jobid import make_job_id``.

PR-3 will rewrite those imports to point at jobpipe.shared.jobid directly,
at which point this shim can be removed.
"""

from jobpipe.shared.jobid import (  # noqa: F401  PR-1 re-export
    canonical_url,
    make_job_id,
)
