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
import time
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


# ── Frame-aware locator resolution (frame + shadow-DOM piercing, Task 1) ────
#
# Every primitive below only ever searched ``page.locator(selector)`` — the
# top document. That misses any field rendered inside an iframe (an embed of
# a company's own careers page, or an ATS widget that mounts its form in a
# child frame). Playwright's own CSS engine already pierces *open* shadow
# roots for a same-document ``page.locator()`` call, so no extra work is
# needed there — but it does NOT reach into iframes on its own; that needs
# the explicit ``frame_locator()`` API per-frame.

def _iter_frame_locators(page: Any, selector: str) -> list:
    """Return a locator for ``selector`` scoped to each iframe on the page,
    in DOM order, via Playwright's ``frame_locator()`` API.

    Called only AFTER the top-document locator for this selector has already
    failed to match — the top document stays the fast, common-case path with
    no behavior change; this is purely a fallback. Counting the iframes or
    building any one frame's locator can fail (a cross-origin restriction, a
    detached iframe, or — in unit tests — a stub Page with no frame support
    at all); every failure here is swallowed, contributing fewer candidates
    rather than raising, exactly like a top-document selector miss.
    """
    try:
        frame_count = page.locator("iframe").count()
    except Exception:
        return []
    locators = []
    for i in range(frame_count):
        try:
            locators.append(page.frame_locator("iframe").nth(i).locator(selector))
        except Exception:
            continue
    return locators


# ── Field-fill primitives (sync, swallow Playwright exceptions per-selector) ─

def fill_text(page: Any, selectors: list[str], value: str,
              *, log: logging.Logger | None = None) -> bool:
    """Try each selector in order (top document, then each iframe); click +
    fill the first visible match.

    Returns True on first successful fill, False if every selector/frame
    combination misses. Each per-candidate failure (timeout, not-visible,
    detached, inaccessible frame) is swallowed silently — the next candidate
    gets a turn.
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
            pass
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(value)
                    log.info(f"Filled via {selector} (iframe)")
                    return True
            except Exception:
                continue
    log.debug("No selector matched for fill_text")
    return False


def _wait_for_upload_confirmation(
    page: Any, filename: str, *,
    timeout_s: float, poll_interval_s: float,
    log: logging.Logger | None = None,
) -> bool:
    """Poll for evidence the ATS actually accepted the just-uploaded file —
    the filename rendering somewhere in the DOM (a sibling/parent element's
    text updating to show it) — bounded by ``timeout_s``.

    ``set_input_files`` not raising only proves Playwright handed the browser
    a file; it says nothing about the ATS's own async upload/parse step ever
    completing (P0 review item 9 — the review that also produced
    submit-truth-gate). Checks the top document then every iframe on each
    pass, the same frame-aware resolution every other primitive in this
    module uses (a company embed could render the upload widget inside a
    frame). Every failure — a filename that breaks the ``text=`` selector
    syntax, a detached node, an inaccessible frame — is swallowed; a timeout
    with no evidence returns ``False`` rather than raising, and NEVER treats
    "no evidence yet" as success.
    """
    log = log or logger
    confirm_selector = f"text={filename}"
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if page.locator(confirm_selector).count() > 0:
                return True
        except Exception:
            pass
        for frame_loc in _iter_frame_locators(page, confirm_selector):
            try:
                if frame_loc.count() > 0:
                    return True
            except Exception:
                continue
        if time.monotonic() >= deadline:
            log.debug(f"No upload-completion evidence for {filename!r} within {timeout_s}s")
            return False
        time.sleep(poll_interval_s)


def upload_file(
    page: Any, selectors: list[str], file_path: str,
    *, log: logging.Logger | None = None,
    confirm_timeout_s: float = 3.0,
    confirm_poll_interval_s: float = 0.1,
) -> tuple[bool, bool]:
    """Upload ``file_path`` into the first matching ``input[type=file]``
    (top document, then each iframe), then wait for evidence the ATS
    actually accepted it before calling the upload confirmed.

    Uses ``count() > 0`` rather than ``is_visible()`` because file inputs are
    frequently visually hidden behind a styled drop zone — they exist in the
    DOM but never report as visible.

    Returns ``(matched, confirmed)``:
      - ``matched`` — a selector resolved to a file input and
        ``set_input_files`` didn't raise. This was the ENTIRE old contract
        (a bare bool) — kept as the first tuple element so a caller that
        only cares "did we even find the input" doesn't regress.
      - ``confirmed`` — within ``confirm_timeout_s`` (default a few seconds,
        deliberately NOT the old 15s ``networkidle`` scale), evidence
        appeared that the ATS accepted the file (see
        ``_wait_for_upload_confirmation``). ``False`` on timeout even when
        ``matched`` is ``True`` — a selector match and a non-raising
        Playwright call are not proof the ATS's async upload/parse ever
        completed, and a timed-out upload must be reported as NOT filled,
        not silently counted as a success (P0 review item 9).
      - ``matched`` is always ``False`` when no selector matched at all —
        ``confirmed`` is then also ``False`` (there was nothing to confirm).
    """
    log = log or logger
    filename = Path(file_path).name
    for selector in selectors:
        try:
            file_input = page.locator(selector).first
            if file_input.count() > 0:
                file_input.set_input_files(file_path)
                log.info(f"Uploaded via {selector}")
                confirmed = _wait_for_upload_confirmation(
                    page, filename, log=log,
                    timeout_s=confirm_timeout_s,
                    poll_interval_s=confirm_poll_interval_s,
                )
                if not confirmed:
                    log.warning(
                        f"Upload via {selector} not confirmed within "
                        f"{confirm_timeout_s}s - ATS may not have accepted the file"
                    )
                return True, confirmed
        except Exception:
            pass
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                file_input = frame_loc.first
                if file_input.count() > 0:
                    file_input.set_input_files(file_path)
                    log.info(f"Uploaded via {selector} (iframe)")
                    confirmed = _wait_for_upload_confirmation(
                        page, filename, log=log,
                        timeout_s=confirm_timeout_s,
                        poll_interval_s=confirm_poll_interval_s,
                    )
                    if not confirmed:
                        log.warning(
                            f"Upload via {selector} (iframe) not confirmed within "
                            f"{confirm_timeout_s}s - ATS may not have accepted the file"
                        )
                    return True, confirmed
            except Exception:
                continue
    return False, False


def paste_textarea(page: Any, selectors: list[str], text: str,
                   *, log: logging.Logger | None = None) -> bool:
    """Paste ``text`` into the first matching textarea or contenteditable
    element (top document, then each iframe). Same iteration semantics as
    ``fill_text``."""
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
            pass
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(text)
                    log.info(f"Pasted via {selector} (iframe)")
                    return True
            except Exception:
                continue
    return False


def select_option(page: Any, selectors: list[str], value: str,
                  *, log: logging.Logger | None = None) -> bool:
    """Choose ``value`` in the first matching visible ``<select>`` (top
    document, then each iframe).

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
            pass
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    el.select_option(value)
                    log.info(f"Selected via {selector} (iframe)")
                    return True
            except Exception:
                continue
    return False


# ── Combobox primitive (react-select / role="combobox" div fields, Task 2) ─
#
# ``fill_text`` / ``select_option`` only understand real ``<input>`` /
# ``<select>`` elements. A react-select-style widget (or any ARIA 1.2
# combobox pattern) is a click-to-open div/input pair with a synthetic
# listbox popup — the demographic, work-authorization, and location
# autocomplete fields Ashby and Greenhouse render this way silently get
# skipped by every primitive above and never even show up in
# ``required_empty`` (nothing tried to fill them, so nothing failed either).
#
# NOTE: the listbox-options search below is intentionally top-document-only
# (no iframe fan-out) — react-select-style listboxes are commonly rendered
# via a portal appended to ``document.body`` rather than as a DOM descendant
# of the combobox container, which usually breaks any attempt to scope the
# search inside a same-origin iframe's own document anyway. The combobox
# CONTAINER itself still gets the full frame-aware search every other
# primitive here uses; only the popup-options lookup is top-document-only.
# This is a known, documented limitation, not an oversight — accepted per
# the task brief (no iframe-embedded ATS combobox is in scope for this pass).

def _wait_for_listbox_options(
    page: Any, *, timeout_s: float, poll_interval_s: float,
    option_selector: str = '[role="option"]',
) -> list[dict]:
    """Poll (bounded by ``timeout_s``) for the combobox's listbox options to
    render, returning ``[{"locator": <option>, "text": str}, ...]`` once at
    least one option is found, or ``[]`` on timeout.

    A fresh click frequently opens the popup a beat before React finishes
    rendering its contents, so a single immediate ``count()`` check would
    miss options that appear a moment later. Every per-poll failure (bad
    selector, detached popup) is swallowed and just contributes another
    empty poll rather than raising.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            loc = page.locator(option_selector)
            count = loc.count()
        except Exception:
            count = 0
        if count:
            options: list[dict] = []
            for i in range(count):
                try:
                    opt = loc.nth(i)
                    text = opt.text_content() or ""
                except Exception:
                    continue
                options.append({"locator": opt, "text": text})
            if options:
                return options
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll_interval_s)


def _best_matching_combobox_option(options: list[dict], value: str) -> dict | None:
    """Pick the option whose text best matches ``value``: an exact
    (case-insensitive, whitespace-trimmed) match wins first; otherwise the
    first option whose text contains ``value`` as a case-insensitive
    substring. Returns ``None`` when nothing matches at all."""
    target = (value or "").strip().lower()
    if not target:
        return None
    for opt in options:
        if opt["text"].strip().lower() == target:
            return opt
    for opt in options:
        if target in opt["text"].strip().lower():
            return opt
    return None


def _combobox_selection_confirmed(display: str, value: str, chosen_text: str) -> bool:
    """Verify the click stuck by comparing the post-selection display value
    (``input_value()`` for a real ``<input>``, or ``text_content()``/chip
    text for a div-wrapper widget — see ``_fill_combobox``) against what we
    typed and what we clicked."""
    d = (display or "").strip().lower()
    if not d:
        return False
    return d == (value or "").strip().lower() or chosen_text.strip().lower() in d


def _fill_combobox(
    page: Any, container: Any, value: str, *,
    log: logging.Logger,
    listbox_timeout_s: float,
    listbox_poll_interval_s: float,
) -> bool:
    """Open ``container``, type ``value`` into it (or a nested input if the
    container itself rejects ``.fill()``), wait for the listbox to render,
    click the best-matching option, and verify the selection stuck. Returns
    False (never raises) at any step that fails, letting the caller fall
    through to the next candidate selector/frame."""
    try:
        container.click()
    except Exception:
        return False

    typed = False
    try:
        container.fill(value)
        typed = True
    except Exception:
        pass
    if not typed:
        try:
            container.locator("input").first.fill(value)
            typed = True
        except Exception:
            pass
    if not typed:
        return False

    options = _wait_for_listbox_options(
        page, timeout_s=listbox_timeout_s, poll_interval_s=listbox_poll_interval_s,
    )
    if not options:
        log.debug("Combobox listbox never rendered any options")
        return False

    best = _best_matching_combobox_option(options, value)
    if best is None:
        log.debug(f"No combobox option matched {value!r}")
        return False

    try:
        best["locator"].click()
    except Exception:
        return False

    # Read back whichever signal actually reflects the post-selection value.
    # Two ARIA-1.2 combobox shapes are in scope (see the module note above):
    #   - ``role="combobox"`` directly on the real ``<input>`` — its
    #     ``.value`` is what changes on selection, and per the DOM spec
    #     ``textContent`` on an ``<input>`` is ALWAYS the empty string
    #     regardless of ``.value`` (verified against real headless
    #     Playwright). ``input_value()`` is the only signal that works here.
    #   - a div/container wrapper around a nested input — the container
    #     itself has no ``.value`` (``input_value()`` raises: "Node is not
    #     an <input>, <textarea> or <select> element"), and the chip/
    #     selected-value text lives in the container's ``text_content()``.
    # Try ``input_value()`` first since it's the correct signal for the
    # input-direct shape and simply isn't supported on a div; fall back to
    # ``text_content()`` for the wrapper shape (or if the input reads back
    # empty for some other reason).
    display = ""
    try:
        display = container.input_value() or ""
    except Exception:
        display = ""
    if not display:
        try:
            display = container.text_content() or ""
        except Exception:
            pass
    return _combobox_selection_confirmed(display, value, best["text"])


def select_combobox(
    page: Any, selectors: list[str], value: str,
    *, log: logging.Logger | None = None,
    listbox_timeout_s: float = 2.0,
    listbox_poll_interval_s: float = 0.1,
) -> bool:
    """Fill a react-select / ``role="combobox"`` div-based field (top
    document, then each iframe for the container itself).

    Pattern per candidate selector: click the combobox container to open it;
    type ``value`` into it (or into a nested ``input`` if the container
    itself isn't fillable — see ``_fill_combobox``); poll for the rendered
    listbox's options (``_wait_for_listbox_options``); pick the
    exact-match-preferred / substring-fallback option
    (``_best_matching_combobox_option``); click it; and verify the selection
    stuck by reading back the post-selection value — ``input_value()`` for
    the ARIA shape where ``role="combobox"`` sits directly on the real
    ``<input>``, falling back to ``text_content()`` for the div-wrapper
    shape (see ``_fill_combobox``'s in-line comment for why one signal alone
    can't cover both shapes; ``_combobox_selection_confirmed`` does the
    actual comparison). Same per-selector exception-swallowing contract as
    every other primitive in this module: any failure at any step just moves
    on to the next candidate rather than raising.
    """
    log = log or logger
    for selector in selectors:
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=1000):
                if _fill_combobox(
                    page, el, value, log=log,
                    listbox_timeout_s=listbox_timeout_s,
                    listbox_poll_interval_s=listbox_poll_interval_s,
                ):
                    log.info(f"Selected combobox option via {selector}")
                    return True
        except Exception:
            pass
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    if _fill_combobox(
                        page, el, value, log=log,
                        listbox_timeout_s=listbox_timeout_s,
                        listbox_poll_interval_s=listbox_poll_interval_s,
                    ):
                        log.info(f"Selected combobox option via {selector} (iframe)")
                        return True
            except Exception:
                continue
    log.debug("No selector matched for select_combobox")
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
    """Re-read the current value at the first selector that yields one (top
    document, then each iframe — same frame-aware resolution as the fill
    primitives above).

    Tries each selector/candidate in the same order the fill primitives
    used, calling ``input_value()`` (works for ``input`` / ``textarea`` /
    ``select``, not ``input[type=file]`` — file inputs are verified via
    ``upload_file``'s own count()-based check, not a DOM value read).
    Any exception (no match, detached node, wrong element type,
    inaccessible frame) is swallowed and the next candidate gets a turn;
    returns ``""`` if nothing yields a non-empty value.

    This MUST use the same frame-aware resolution as the fill primitives —
    a value written into an iframe field would otherwise read back empty
    against the top document and P0's DOM re-read verification would
    wrongly demote a successful frame-fill back to "didn't stick".
    """
    log = log or logger
    for selector in selectors:
        try:
            value = page.locator(selector).first.input_value()
        except Exception:
            value = ""
        if value:
            return value
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                value = frame_loc.first.input_value()
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
