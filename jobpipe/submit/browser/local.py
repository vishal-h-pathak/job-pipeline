"""browser/local.py — persistent, logged-in local browser for the prepare flow.

The real submit runtime is local + visible (``jobpipe-submit`` on the user's
machine). This module centralises the one decision every prepare-flow entry
point shares: *how* to obtain a Playwright ``BrowserContext*``.

Three strategies, in priority order:

  1. **CDP attach** — when ``JOBPIPE_BROWSER_CDP`` is set, attach to a Chrome
     the user launched themselves (``--remote-debugging-port=…``) and reuse its
     existing context. An explicit opt-in for "drive my actual everyday
     browser"; never closes that browser on teardown. CDP wins over the
     headless flag because attaching to a running Chrome is inherently visible.
  2. **Persistent context (default, visible)** — ``launch_persistent_context``
     against a profile dir (``JOBPIPE_BROWSER_PROFILE``, default
     ``~/.jobpipe/chrome-profile``) so cookies / ATS logins persist across
     runs. The user logs into their ATS accounts once in that profile.
  3. **Headless fallback** — when ``HEADLESS`` is truthy or there is no display
     (CI, tests), the old cookieless ``launch()`` + ``new_context()`` path, so
     the suite and any headless runner keep working.

``open_browser_context`` returns ``(context, closer)``. The caller opens one
context per run and a new *page (tab)* per job in that same window — matching
"opens in a new tab" and sharing logins across all jobs in the run. ``closer``
tears down the right object for the strategy (persistent context owns its
window; the cookieless path owns a Browser; CDP owns neither).

Env is read live (not captured at import) so a process/test can set the knobs
after this module is imported.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable, Tuple

from jobpipe.config import (
    BROWSER_CDP_ENV,
    BROWSER_PROFILE_DEFAULT,
    BROWSER_PROFILE_ENV,
)

logger = logging.getLogger("submit.browser.local")

_VIEWPORT = {"width": 1280, "height": 900}
# Only spoofed on the cookieless fallback path; a persistent/real profile
# already reports a genuine Chrome UA, which is what we want it to look like.
_FALLBACK_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
)


def cdp_endpoint() -> str:
    """The configured CDP endpoint, or '' when not attaching to a user Chrome."""
    return os.environ.get(BROWSER_CDP_ENV, "").strip()


def profile_dir() -> Path:
    """The persistent Chrome profile directory (expanded), default
    ``~/.jobpipe/chrome-profile``."""
    raw = os.environ.get(BROWSER_PROFILE_ENV) or BROWSER_PROFILE_DEFAULT
    return Path(raw).expanduser()


def _has_display() -> bool:
    """Best-effort: is there a GUI to render a visible browser into?

    macOS / Windows always have one. On Linux a visible browser needs an X
    or Wayland display; without one we must run headless.
    """
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def is_headless() -> bool:
    """Decide headless vs visible for the local prepare flow.

    An explicit ``HEADLESS`` env var wins (``1|true|yes|on`` → headless,
    anything else → visible). With no ``HEADLESS`` set, default to VISIBLE —
    unless there is no display to render into (then headless so CI doesn't
    hang). This is independent of ``config.HEADLESS`` (which defaults true and
    governs the retired Browserbase path).
    """
    raw = os.environ.get("HEADLESS")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return not _has_display()


def open_browser_context(pw, *, headless: bool) -> Tuple[object, Callable[[], None]]:
    """Open a Playwright context for the prepare flow; return ``(context, closer)``.

    ``pw`` is the object yielded by ``sync_playwright()``. ``headless`` is the
    caller's effective decision (usually :func:`is_headless`); ``open`` honours
    it for the launch fallback but a configured CDP endpoint overrides it.
    """
    cdp = cdp_endpoint()
    if cdp:
        logger.info("Attaching to user Chrome over CDP: %s", cdp)
        browser = pw.chromium.connect_over_cdp(cdp)
        contexts = list(getattr(browser, "contexts", []) or [])
        context = contexts[0] if contexts else browser.new_context(viewport=_VIEWPORT)
        # Never close the user's own browser — they own its lifecycle.
        return context, (lambda: None)

    if headless:
        logger.info("Launching headless cookieless browser (fallback path)")
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport=_VIEWPORT, user_agent=_FALLBACK_UA)
        return context, browser.close

    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    logger.info("Launching persistent visible browser, profile=%s", profile)
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
        viewport=_VIEWPORT,
    )
    return context, context.close
