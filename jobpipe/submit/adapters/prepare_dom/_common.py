"""prepare_dom/_common.py — Sync Playwright DOM helpers for the prepare-only
adapters (PR-7).

Sibling: ``submit/adapters/_common.py`` holds the async Stagehand+Browserbase
helpers used by the deterministic submitters. Both files exist because the two
adapter tracks have incompatible runtimes; merging them would obscure the call
signatures (sync ``Page.locator().fill()`` returning bool vs async
``sh_act(sess, …)`` mutating a ``SubmissionResult``).

Each helper here was extracted from the per-ATS prepare_dom adapters in PR-7.
The behavior of every helper is identical to the inline implementation it
replaces — same selector iteration order, same exception swallowing, same
return contracts. The only runtime delta is the logger name on the success
log line: ``fill_text`` / ``upload_file`` / ``paste_textarea`` accept an
optional ``log`` argument and each adapter passes its own per-ATS logger
(``prepare_dom.ashby``, ``prepare_dom.lever``, ``prepare_dom.greenhouse``) so
log filtering by ATS keeps working.

Playwright is NOT imported at module top — type hints reference ``"Page"`` as
a forward string and the runtime methods (``page.locator``, ``el.click``,
``el.fill``, ``el.set_input_files``, ``el.is_visible``, ``el.count``) are
duck-typed. This lets ``tests/test_prepare_dom_common.py`` exercise every
helper with a stub Page object and no real Playwright install.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Page  # noqa: F401

logger = logging.getLogger("prepare_dom._common")


# ── Selector builders ──────────────────────────────────────────────────────

def label_selectors(label_text: str) -> list[str]:
    """The four label/aria/placeholder selectors that every ATS adapter falls
    back to when name-attr resolution misses. Order is preserved verbatim from
    the prior per-adapter inline implementations."""
    return [
        f'label:has-text("{label_text}") input',
        f'label:has-text("{label_text}") >> input',
        f'input[aria-label="{label_text}"]',
        f'input[placeholder*="{label_text}" i]',
    ]


def name_attr_selectors(name_map: dict, label_text: str) -> list[str]:
    """Return the [input, textarea] name-attr selector pair for ``label_text``,
    or an empty list if ``name_map`` has no entry. Used by Lever and Greenhouse
    where the canonical input ``name`` is known per-label (e.g. Lever's
    ``urls[LinkedIn]`` or Greenhouse's ``job_application[first_name]``)."""
    name = name_map.get(label_text)
    if not name:
        return []
    return [f'input[name="{name}"]', f'textarea[name="{name}"]']


# ── Field-fill primitives (sync, swallow Playwright exceptions per-selector) ─

def fill_text(page: Any, selectors: list[str], value: str,
              *, log: logging.Logger | None = None) -> bool:
    """Try each selector in order; click + fill the first visible match.

    Returns True on first successful fill, False if every selector misses.
    Each per-selector failure (timeout, not-visible, detached) is swallowed
    silently — the next selector gets a turn.
    """
    log = log or logger
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=1000):
                el.click()
                el.fill(value)
                log.info(f"Filled via {selector}")
                return True
        except Exception:
            continue
    log.debug("No selector matched for fill_text")
    return False


def upload_file(page: Any, selectors: list[str], file_path: str,
                *, log: logging.Logger | None = None) -> bool:
    """Upload ``file_path`` into the first matching ``input[type=file]``.

    Uses ``count() > 0`` rather than ``is_visible()`` because file inputs are
    frequently visually hidden behind a styled drop zone — they exist in the
    DOM but never report as visible.
    """
    log = log or logger
    for selector in selectors:
        try:
            file_input = page.locator(selector).first
            if file_input.count() > 0:
                file_input.set_input_files(file_path)
                log.info(f"Uploaded via {selector}")
                return True
        except Exception:
            continue
    return False


def paste_textarea(page: Any, selectors: list[str], text: str,
                   *, log: logging.Logger | None = None) -> bool:
    """Paste ``text`` into the first matching textarea or contenteditable
    element. Same iteration semantics as ``fill_text``."""
    log = log or logger
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=1000):
                el.click()
                el.fill(text)
                log.info(f"Pasted via {selector}")
                return True
        except Exception:
            continue
    return False


def select_option(page: Any, selectors: list[str], value: str,
                  *, log: logging.Logger | None = None) -> bool:
    """Choose ``value`` in the first matching visible ``<select>``.

    Same iteration / exception-swallowing semantics as ``fill_text``. Used by
    the declarative field-map layer for ``type: select`` specs (none of the
    current per-ATS maps use one, but the format and primitive support it so
    adding a dropdown field is a config edit, not a code change). The value is
    passed straight to Playwright's ``select_option`` — pass the option's
    ``value`` attribute text.
    """
    log = log or logger
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=1000):
                el.select_option(value)
                log.info(f"Selected via {selector}")
                return True
        except Exception:
            continue
    return False


# ── DOM re-read verification (honest fill verification, P0 #2) ────────────
#
# ``fill_text`` / ``paste_textarea`` / ``select_option`` only report whether
# a selector matched and the Playwright call didn't raise — a React form
# that silently discards ``.fill()``, or a masked input that mangles the
# value, still counts as "filled" under that contract. These helpers re-read
# the LIVE DOM after a fill claims success so a fill that didn't stick can be
# demoted back to unfilled.

def read_value(page: Any, selectors: list[str],
               *, log: logging.Logger | None = None) -> str:
    """Re-read the current value at the first selector that yields one.

    Tries each selector in the same order the fill primitives used, calling
    ``input_value()`` (works for ``input`` / ``textarea`` / ``select``, not
    ``input[type=file]`` — file inputs are verified via
    ``upload_file``'s own count()-based check, not a DOM value read).
    Any exception (no match, detached node, wrong element type) is
    swallowed and the next selector gets a turn; returns ``""`` if nothing
    yields a non-empty value.
    """
    log = log or logger
    for selector in selectors:
        try:
            value = page.locator(selector).first.input_value()
        except Exception:
            continue
        if value:
            return value
    return ""


def _resolve_dom_label(page: Any, el: Any) -> str:
    """Best-effort human label for a required-flagged DOM element: prefer
    ``aria-label`` / ``placeholder`` / ``name``, then fall back to the text
    of an associated ``<label for=id>``."""
    for attr in ("aria-label", "placeholder", "name"):
        try:
            value = el.get_attribute(attr)
        except Exception:
            value = None
        if value:
            return value.strip()
    try:
        el_id = el.get_attribute("id")
    except Exception:
        el_id = None
    if el_id:
        try:
            lbl = page.locator(f'label[for="{el_id}"]').first
            if lbl.count() > 0:
                text = lbl.text_content()
                if text and text.strip():
                    return text.strip()
        except Exception:
            pass
    return ""


def _resolve_dom_selector(el: Any) -> str:
    """A selector that can re-find this exact element for a later value
    read — ``#id`` when available, else ``[name=...]``, else ``""`` (the
    field is still counted as required, just un-re-readable)."""
    try:
        el_id = el.get_attribute("id")
    except Exception:
        el_id = None
    if el_id:
        return f'[id="{el_id}"]'
    try:
        name = el.get_attribute("name")
    except Exception:
        name = None
    if name:
        return f'[name="{name}"]'
    return ""


def scan_required_fields(page: Any, *,
                         log: logging.Logger | None = None) -> list[dict]:
    """Scan the LIVE DOM for every element flagged required (the
    ``required`` attribute or ``aria-required="true"``), deduped by
    resolved label.

    This is what lets the verification pass build the required-set from
    the form itself instead of the ~5 hardcoded ``field_maps.yml`` labels
    — custom / role-specific questions the ATS marks required show up here
    even though the declarative field map never heard of them. Best-effort
    throughout: a single element's read failing is skipped, and a total
    scan failure (unsupported selector, detached page) degrades to "no
    DOM-required fields found" rather than raising.

    Returns ``[{"label": str, "selectors": [selector]}, ...]``.
    """
    log = log or logger
    selector = '[required], [aria-required="true"]'
    try:
        count = page.locator(selector).count()
    except Exception:
        return []

    found: dict[str, str] = {}
    for i in range(count):
        try:
            el = page.locator(selector).nth(i)
            label = _resolve_dom_label(page, el)
            el_selector = _resolve_dom_selector(el)
        except Exception:
            continue
        if label and label not in found:
            found[label] = el_selector

    return [
        {"label": label, "selectors": [sel] if sel else []}
        for label, sel in found.items()
    ]


# ── Cover-letter source resolution ─────────────────────────────────────────

def load_cover_letter(cover_letter_path_or_text: str) -> str:
    """Resolve a cover-letter argument that may be a file path OR raw text.

    Behavior preserved verbatim from the per-adapter ``_load_cover_letter``:
        - If the value is an existing file path → read and return its text.
        - Else, if the value is longer than 100 chars → treat as inline text.
        - Else → return empty string (covers both "missing path" and "stub
          string too short to be a real cover letter").

    Empty / None inputs return empty string instead of raising on
    ``Path(None)``; callers in the prior implementation guarded with
    ``if cover_letter_path:`` before calling, so this is a defensive
    no-op for them but lets the helper be unit-tested directly.
    """
    if not cover_letter_path_or_text:
        return ""
    path = Path(cover_letter_path_or_text)
    # ``path.exists()`` calls ``os.stat()``, which raises
    # ``OSError [Errno 63] File name too long`` on macOS / Linux when
    # the argument is a multi-thousand-character cover-letter string
    # (typical 2k-char cover letters trip macOS PATH_MAX of ~1024).
    # Catch the OSError and fall through to the inline-text branch so
    # callers passing a long body get the body back unchanged.
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    if len(cover_letter_path_or_text) > 100:
        return cover_letter_path_or_text
    return ""


# ── Field-map builder (M-1 form_answers → label-keyed dict) ────────────────

def build_field_map(job: dict) -> dict[str, str]:
    """Build a label-keyed dict of values from ``job["form_answers"]``.

    Identity / contact / location / comp values come from the M-1
    form_answers JSON (which itself was filled from profile.yml in
    Python — never LLM-generated). Each per-ATS handler reuses this
    same source while keeping its own selector strategy.

    Moved from ``prepare_dom/ashby.py::_build_field_map`` in PR-7. The
    set of label keys and their mapping is unchanged — Lever previously
    overrode the ``Name`` and ``Full Name`` keys to prefer ``full_name``
    explicitly, and that override is preserved at the Lever adapter
    level because it has the form-specific knowledge.
    """
    fa = job.get("form_answers") or {}
    return {
        "First Name": fa.get("first_name") or "",
        "Last Name": fa.get("last_name") or "",
        "Full Name": fa.get("full_name") or "",
        "Name": fa.get("full_name") or "",
        "Email": fa.get("email") or "",
        "Phone": fa.get("phone") or "",
        "LinkedIn URL": fa.get("linkedin_url") or "",
        "LinkedIn": fa.get("linkedin_url") or "",
        "GitHub URL": fa.get("github_url") or "",
        "GitHub": fa.get("github_url") or "",
        "Portfolio": fa.get("portfolio_url") or "",
        "Website": fa.get("portfolio_url") or "",
        "Location": fa.get("current_location") or "",
        "Current Location": fa.get("current_location") or "",
        "City": fa.get("current_location") or "",
        "Current Company": fa.get("current_company") or "",
        "Company": fa.get("current_company") or "",
        "Current Title": fa.get("current_title") or "",
        "Title": fa.get("current_title") or "",
    }


# ── Notes-list helper for unfilled custom questions ────────────────────────

def note_unfilled_custom_questions(job: dict, notes_parts: list) -> None:
    """Append a one-line note to ``notes_parts`` when the job has any
    role-specific questions that the prepare_dom adapters intentionally do
    NOT auto-fill (project policy: humans paste these from cockpit drafts).

    Lever and Greenhouse both surface this message; Ashby's prior
    implementation did not (verified in the PR-7 audit). The helper exists
    so adopting the note in Ashby later is a one-line call rather than a
    five-line copy-paste.
    """
    qs = (job.get("form_answers") or {}).get("additional_questions") or []
    if qs:
        notes_parts.append(
            f"{len(qs)} role-specific question(s) NOT auto-filled - "
            f"paste from cockpit drafts"
        )
