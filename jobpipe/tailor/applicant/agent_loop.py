"""applicant/agent_loop.py — re-export shim (PR-4).

The implementation moved to ``jobpipe.submit.adapters.prepare_loop``.
This shim is here only so unmigrated callers that still write
``from applicant.agent_loop import run_submission_agent`` keep working
through the multi-PR migration. PR-9 finishes the cutover.
"""

from jobpipe.submit.adapters.prepare_loop import (  # noqa: F401
    run_submission_agent,
    TOOL_SCHEMAS,
)

__all__ = ["run_submission_agent", "TOOL_SCHEMAS"]
