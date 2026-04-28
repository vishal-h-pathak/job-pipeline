"""applicant/browser_tools.py — re-export shim (PR-7).

The implementation moved to ``jobpipe.submit.adapters.browser_tools``. Both the
``BrowserSession`` dataclass and the ``_ENUMERATE_JS`` snippet are re-exported
here so unmigrated callers (legacy scripts, plus any bare-import call site
resolved via ``jobpipe.shared.ats_detect._bootstrap_tailor_sys_path``) keep
working until PR-9 finishes the cutover.
"""

from jobpipe.submit.adapters.browser_tools import (  # noqa: F401
    BrowserSession,
    _ENUMERATE_JS,
)

__all__ = ["BrowserSession", "_ENUMERATE_JS"]
