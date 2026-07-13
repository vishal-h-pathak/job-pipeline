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


def _iter_frame_roots(page: Any) -> list:
    """Return each iframe's own ``FrameLocator`` (not scoped to any
    selector) in DOM order — the "same document as this element" root
    ``_resolve_dom_label`` needs to find a ``<label for=id>`` that lives
    inside the iframe rather than the top document. Same best-effort
    swallow-and-degrade contract as ``_iter_frame_locators``."""
    try:
        frame_count = page.locator("iframe").count()
    except Exception:
        return []
    roots = []
    for i in range(frame_count):
        try:
            roots.append(page.frame_locator("iframe").nth(i))
        except Exception:
            continue
    return roots


# ── Field-fill primitives (sync, swallow Playwright exceptions per-selector) ─
#
# Task 3 (fill_report / #1): every primitive below gained an optional
# ``misses: list | None = None`` keyword. When a caller passes a list, the
# primitive appends one entry per selector CANDIDATE it tried:
#   - a failure (the try/except block actually raised) appends
#     ``{"selector": ..., "error": type(exc).__name__}`` — this is the shape
#     ``apply_field_map``'s ``fill_report`` reports as ``misses``.
#   - a SUCCESS also appends one entry, ``{"selector": ...}`` (no "error"
#     key), immediately before returning — this is how ``apply_field_map``
#     recovers ``matched_selector`` without changing any primitive's public
#     return type (still a bare bool / the existing ``upload_file`` tuple).
#     ``apply_field_map`` partitions the list itself: entries carrying
#     "error" become the report's ``misses``; a trailing entry WITHOUT
#     "error" is the match.
# A selector that was simply never visible (no exception at all) leaves no
# trace — exactly like before Task 3 — since there is no exception class to
# report and "not visible yet" is not itself a failure worth an entry.
# Default ``None`` means "don't bother": existing callers/tests that never
# pass ``misses`` see byte-identical behavior.

def fill_text(page: Any, selectors: list[str], value: str,
              *, log: logging.Logger | None = None,
              misses: list | None = None) -> bool:
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
                if misses is not None:
                    misses.append({"selector": selector})
                return True
        except Exception as exc:
            if misses is not None:
                misses.append({"selector": selector, "error": type(exc).__name__})
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(value)
                    log.info(f"Filled via {selector} (iframe)")
                    if misses is not None:
                        misses.append({"selector": selector})
                    return True
            except Exception as exc:
                if misses is not None:
                    misses.append({"selector": selector, "error": type(exc).__name__})
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
    misses: list | None = None,
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
                if misses is not None:
                    misses.append({"selector": selector})
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
        except Exception as exc:
            if misses is not None:
                misses.append({"selector": selector, "error": type(exc).__name__})
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                file_input = frame_loc.first
                if file_input.count() > 0:
                    file_input.set_input_files(file_path)
                    log.info(f"Uploaded via {selector} (iframe)")
                    if misses is not None:
                        misses.append({"selector": selector})
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
            except Exception as exc:
                if misses is not None:
                    misses.append({"selector": selector, "error": type(exc).__name__})
                continue
    return False, False


def _fill_first_scoped_textarea(
    locator: Any, text: str, needles: tuple[str, ...], *,
    page: Any, log: logging.Logger, selector: str, frame_suffix: str,
    misses: list | None, label_root: Any = None,
) -> bool:
    """Enumerate EVERY element behind ``locator`` — mirrors
    ``scan_required_fields``'s ``count()`` + ``nth(i)`` pattern rather than
    ``.first`` — and fill the first one that is both visible and whose
    resolved label matches one of ``needles``.

    This is what lets the bare catch-all ``"textarea"`` selector find a
    correctly labeled cover-letter box that renders AFTER an earlier,
    unlabeled/mislabeled textarea on the same page: checking only ``.first``
    (the pre-fix behavior) would stop at the wrong element and never look
    any further, silently filling nothing even though a valid cover-letter
    textarea exists later in DOM order.

    Any failure enumerating or reading an individual candidate (a detached
    node, ``is_visible``/label-resolution blowing up, the fill itself
    raising) is swallowed, recorded in ``misses`` when given, and the next
    candidate in DOM order is tried — same per-candidate exception contract
    as every other primitive in this module. A total ``count()`` failure
    (the selector itself is invalid, or a broken locator) is likewise
    swallowed and reported as a single miss rather than propagating.

    ``label_root`` — the ``page`` (top document) or that iframe's own
    ``FrameLocator`` (see ``_iter_frame_roots``) — is forwarded to
    ``_label_matches_any``/``_resolve_dom_label`` so a ``<label for=id>``
    lookup for a frame-embedded candidate searches that SAME frame's
    document, not always the top page.
    """
    try:
        count = locator.count()
    except Exception as exc:
        if misses is not None:
            misses.append({"selector": selector, "error": type(exc).__name__})
        return False
    for i in range(count):
        try:
            el = locator.nth(i)
            if not el.is_visible(timeout=1000):
                continue
            if not _label_matches_any(page, el, needles, label_root=label_root):
                continue
            el.click()
            el.fill(text)
        except Exception as exc:
            if misses is not None:
                misses.append({"selector": selector, "error": type(exc).__name__})
            continue
        log.info(f"Pasted via {selector}{frame_suffix}")
        if misses is not None:
            misses.append({"selector": selector})
        return True
    return False


def paste_textarea(
    page: Any, selectors: list[str], text: str,
    *, log: logging.Logger | None = None,
    misses: list | None = None,
    scoped_needles: tuple[str, ...] | None = None,
) -> bool:
    """Paste ``text`` into the first matching textarea or contenteditable
    element (top document, then each iframe). Same iteration semantics as
    ``fill_text`` for every UNSCOPED selector.

    ``scoped_needles`` (Task 3 / #3) restricts ONLY the bare catch-all
    ``"textarea"`` selector — every more-specific selector ahead of it in
    the chain (``textarea[name*="cover" i]`` etc.) is tried unconditionally,
    exactly as before. The bare catch-all is the one that used to grab
    whichever textarea happened to render first on the page — often a
    custom question, not the cover letter, on a form with no properly
    labeled cover-letter box.

    When scoped, EVERY element matching the bare ``"textarea"`` selector is
    enumerated in DOM order (``_fill_first_scoped_textarea`` — the
    ``count()`` + ``nth(i)`` pattern, not just ``.first``), and the first
    one whose resolved label (``_resolve_dom_label`` — aria-label /
    placeholder / name / associated ``label[for=id]``) contains a needle,
    case-insensitively, gets filled. This matters when the FIRST textarea
    on the page is an unrelated/mislabeled custom question and the
    correctly labeled cover-letter box renders later — checking only
    ``.first`` would stop at the wrong element and fill nothing at all. If
    no candidate (in the top document or any iframe) matches, this is a
    plain non-match — not a crash, and not a per-selector ``misses`` entry
    either (mirrors the "not visible" precedent: no exception, no entry —
    the field-level ``fill_report`` still shows the spec unfilled).
    """
    log = log or logger
    for selector in selectors:
        scoped = bool(scoped_needles) and selector == "textarea"
        if scoped:
            try:
                top_locator = page.locator(selector)
            except Exception as exc:
                if misses is not None:
                    misses.append({"selector": selector, "error": type(exc).__name__})
                top_locator = None
            if top_locator is not None and _fill_first_scoped_textarea(
                top_locator, text, scoped_needles,
                page=page, log=log, selector=selector, frame_suffix="",
                misses=misses,
            ):
                return True
            for frame_root in _iter_frame_roots(page):
                try:
                    frame_loc = frame_root.locator(selector)
                except Exception as exc:
                    if misses is not None:
                        misses.append(
                            {"selector": selector, "error": type(exc).__name__}
                        )
                    continue
                if _fill_first_scoped_textarea(
                    frame_loc, text, scoped_needles,
                    page=page, log=log, selector=selector,
                    frame_suffix=" (iframe)", misses=misses,
                    label_root=frame_root,
                ):
                    return True
            continue
        try:
            el = page.locator(selector).first
            if el.is_visible(timeout=1000):
                el.click()
                el.fill(text)
                log.info(f"Pasted via {selector}")
                if misses is not None:
                    misses.append({"selector": selector})
                return True
        except Exception as exc:
            if misses is not None:
                misses.append({"selector": selector, "error": type(exc).__name__})
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    el.click()
                    el.fill(text)
                    log.info(f"Pasted via {selector} (iframe)")
                    if misses is not None:
                        misses.append({"selector": selector})
                    return True
            except Exception as exc:
                if misses is not None:
                    misses.append({"selector": selector, "error": type(exc).__name__})
                continue
    return False


def select_option(page: Any, selectors: list[str], value: str,
                  *, log: logging.Logger | None = None,
                  misses: list | None = None) -> bool:
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
                if misses is not None:
                    misses.append({"selector": selector})
                return True
        except Exception as exc:
            if misses is not None:
                misses.append({"selector": selector, "error": type(exc).__name__})
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                el = frame_loc.first
                if el.is_visible(timeout=1000):
                    el.select_option(value)
                    log.info(f"Selected via {selector} (iframe)")
                    if misses is not None:
                        misses.append({"selector": selector})
                    return True
            except Exception as exc:
                if misses is not None:
                    misses.append({"selector": selector, "error": type(exc).__name__})
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
    misses: list | None = None,
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
                    if misses is not None:
                        misses.append({"selector": selector})
                    return True
        except Exception as exc:
            if misses is not None:
                misses.append({"selector": selector, "error": type(exc).__name__})
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
                        if misses is not None:
                            misses.append({"selector": selector})
                        return True
            except Exception as exc:
                if misses is not None:
                    misses.append({"selector": selector, "error": type(exc).__name__})
                continue
    log.debug("No selector matched for select_combobox")
    return False


# ── Tolerant form-readiness wait (Task 3 / #4) ─────────────────────────────
#
# All three prepare_dom adapters used to call
# ``page.wait_for_load_state("networkidle", timeout=15000)`` before filling.
# Analytics-heavy pages (the common case for a careers site) routinely never
# go network-idle within 15s — third-party trackers keep firing requests
# indefinitely — so a perfectly healthy, already-rendered form silently
# degraded into an unnecessary assisted-manual hand-off. This helper waits
# for POSITIVE evidence the form itself exists instead of network silence.

def wait_for_form_ready(
    page: Any, *, timeout_ms: int = 8000,
    settle_s: float = 1.0,
    poll_interval_s: float = 0.2,
    log: logging.Logger | None = None,
) -> dict:
    """Poll (bounded by ``timeout_ms``) for either a required-field element
    (``[required], [aria-required="true"]`` — the same selector
    ``scan_required_fields`` uses) or a bare ``<form>`` tag. Returns as soon
    as either shows up: ``{"ready": True, "signal": "required_field" |
    "form_tag"}``.

    If NEITHER appears before the deadline this is a distinct, named
    outcome rather than a bare ``except: pass`` — falls back to a short
    settle sleep (best-effort; the caller's own ``page.goto`` already fired
    ``domcontentloaded`` before this was ever called) and returns
    ``{"ready": False, "signal": None}``. This never blocks the fill: the
    caller proceeds either way and surfaces ``ready is False`` as
    ``result["readiness_timeout"] = True`` so it reads differently in the
    attempt notes than "the fill ran but missed fields" — a page that never
    renders a `<form>` still gets the same best-effort fill attempt the old
    ``networkidle`` timeout used to fall through to, just without waiting
    the full 15s to get there.
    """
    log = log or logger
    deadline = time.monotonic() + (timeout_ms / 1000)
    checks = (
        ('[required], [aria-required="true"]', "required_field"),
        ("form", "form_tag"),
    )
    while True:
        for selector, signal in checks:
            try:
                if page.locator(selector).count() > 0:
                    return {"ready": True, "signal": signal}
            except Exception:
                pass
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval_s)
    log.debug(f"wait_for_form_ready: no required-field/form evidence within {timeout_ms}ms")
    time.sleep(settle_s)  # last-resort settle before proceeding anyway
    return {"ready": False, "signal": None}


# ── DOM re-read verification (honest fill verification, P0 #2) ────────────
#
# ``fill_text`` / ``paste_textarea`` / ``select_option`` only report whether
# a selector matched and the Playwright call didn't raise — a React form
# that silently discards ``.fill()``, or a masked input that mangles the
# value, still counts as "filled" under that contract. These helpers re-read
# the LIVE DOM after a fill claims success so a fill that didn't stick can be
# demoted back to unfilled.

def _read_first_scoped_textarea_value(
    locator: Any, page: Any, needles: tuple[str, ...], *,
    label_root: Any = None,
) -> str:
    """Re-read the value of the first candidate behind ``locator`` — via
    ``count()`` + ``nth(i)``, the SAME enumeration
    ``_fill_first_scoped_textarea`` used to pick the fill target — whose
    resolved label matches one of ``needles``, regardless of DOM position.

    Exists because a plain ``.first``-based re-read would always check the
    physically first ``<textarea>`` on the page, which is the WRONG
    element whenever the fill actually landed on a later candidate (the
    exact scenario scoped enumeration exists to handle: an earlier
    mislabeled/custom-question textarea, then the correctly labeled cover
    letter box later in DOM order) — that would wrongly demote a fill that
    actually stuck. Any per-candidate failure (label resolution,
    ``input_value()`` raising for a non-input element) is swallowed and the
    next candidate is tried; a total ``count()`` failure or no match at all
    returns ``""``, same as the rest of this module's exception contract.

    ``label_root`` is forwarded to ``_label_matches_any`` — see
    ``_fill_first_scoped_textarea``'s docstring for why a frame-embedded
    candidate needs its OWN frame as the label-resolution root, not always
    the top page.
    """
    try:
        count = locator.count()
    except Exception:
        return ""
    for i in range(count):
        try:
            el = locator.nth(i)
            if not _label_matches_any(page, el, needles, label_root=label_root):
                continue
            value = el.input_value(timeout=1000) or ""
        except Exception:
            continue
        if value:
            return value
    return ""


def read_value(page: Any, selectors: list[str],
               *, log: logging.Logger | None = None,
               scoped_needles: tuple[str, ...] | None = None) -> str:
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

    ``scoped_needles`` (Task 3 finding fix) mirrors ``paste_textarea``'s
    scoping for the bare catch-all ``"textarea"`` selector: when given, that
    selector is re-read via ``_read_first_scoped_textarea_value``'s
    enumeration + label-match order instead of always checking ``.first`` —
    see that helper's docstring for why a plain ``.first`` re-read would
    otherwise wrongly demote a scoped fill that landed on a non-first
    candidate. Every other (unscoped) selector's re-read is completely
    unaffected — default ``None`` is byte-identical to before this fix.
    """
    log = log or logger
    for selector in selectors:
        if scoped_needles and selector == "textarea":
            try:
                top_locator = page.locator(selector)
            except Exception:
                top_locator = None
            value = (
                _read_first_scoped_textarea_value(top_locator, page, scoped_needles)
                if top_locator is not None else ""
            )
            if value:
                return value
            for frame_root in _iter_frame_roots(page):
                try:
                    frame_loc = frame_root.locator(selector)
                except Exception:
                    continue
                value = _read_first_scoped_textarea_value(
                    frame_loc, page, scoped_needles, label_root=frame_root,
                )
                if value:
                    return value
            continue
        try:
            value = page.locator(selector).first.input_value(timeout=1000)
        except Exception:
            value = ""
        if value:
            return value
        for frame_loc in _iter_frame_locators(page, selector):
            try:
                value = frame_loc.first.input_value(timeout=1000)
            except Exception:
                continue
            if value:
                return value
    return ""


def _resolve_dom_label(page: Any, el: Any, *, label_root: Any = None) -> str:
    """Best-effort human label for a required-flagged DOM element: prefer
    ``aria-label`` / ``placeholder`` / ``name``, then fall back to the text
    of an associated ``<label for=id>``.

    ``label_root`` is the object whose ``.locator()`` reaches the SAME
    document ``el`` lives in — ``page`` for a top-document element, or the
    owning ``FrameLocator`` for an element found inside an iframe (see
    ``_iter_frame_roots``). Defaults to ``page`` (correct for every
    top-document caller). Querying ``page.locator(...)`` for a
    ``<label for=id>`` that only exists inside an iframe's own document
    would always find zero matches — this is what let a frame-embedded,
    ``label[for]``-only-resolvable textarea silently fail the scoped
    cover-letter match despite being genuinely reachable via Task 1's
    frame-piercing, and (BLOCKER fix) is exactly why ``scan_required_fields``
    now passes its own per-frame root here too instead of always ``page``.
    """
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
            root = label_root if label_root is not None else page
            lbl = root.locator(f'label[for="{el_id}"]').first
            if lbl.count() > 0:
                text = lbl.text_content()
                if text and text.strip():
                    return text.strip()
        except Exception:
            pass
    return ""


def _label_matches_any(page: Any, el: Any, needles: tuple[str, ...], *,
                       label_root: Any = None) -> bool:
    """True if ``el``'s resolved label (``_resolve_dom_label`` — reused
    as-is, not re-implemented) contains at least one of ``needles``,
    case-insensitively. Used by ``paste_textarea``'s ``scoped_needles`` to
    stop a bare catch-all ``"textarea"`` selector from grabbing the wrong
    (unlabeled or custom-question) textarea on a form with no properly
    labeled cover-letter box. An unresolvable label (``""``) never matches
    — no label text means no needle can be found in it. ``label_root`` is
    forwarded to ``_resolve_dom_label`` — see its docstring."""
    label = _resolve_dom_label(page, el, label_root=label_root)
    if not label:
        return False
    low = label.lower()
    return any(needle in low for needle in needles)


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


def _resolve_dom_kind(el: Any) -> str:
    """``"checked"`` for a radio/checkbox input, else ``"value"``.

    Radio-group required questions share one ``name`` attribute across
    every option — ``_resolve_dom_selector`` necessarily builds a selector
    that matches ALL of them, not just the one the applicant picked. A
    radio/checkbox's ``value`` attribute is a static label ("Yes"/"No"/
    "on"), present whether or not the input is checked — reading it back
    via ``input_value()`` would report a required radio question as
    "already answered" even when nothing was ever selected. ``"checked"``
    tells the caller to test for a checked match (``{selector}:checked``)
    instead of reading a value back.

    Known residual gap: ``scan_required_fields`` dedups a group by its
    resolved LABEL and keeps whichever option's selector it saw first —
    if that option has its own ``id`` (``_resolve_dom_selector`` prefers
    ``[id=...]`` over the shared ``[name=...]``), the ``:checked`` probe
    only covers that one option, so a group answered via a DIFFERENT,
    non-first option with its own id would still read back as unanswered.
    Harmless in the live pre-fill flow today (custom radio questions are
    never auto-answered by any adapter, so a required group is always
    genuinely empty when this gate runs) and fails safe (an unnecessary
    assisted-manual hand-off, never a false "already answered") — but
    would matter for a form that pre-checks a non-first option by
    default. The shared ``[name=...]`` selector is the one that actually
    covers every option in a group; prefer wiring a group-aware selector
    here over an id-scoped one if this ever needs tightening.
    """
    try:
        input_type = (el.get_attribute("type") or "").lower()
    except Exception:
        input_type = ""
    return "checked" if input_type in ("radio", "checkbox") else "value"


def scan_required_fields(page: Any, *,
                         log: logging.Logger | None = None,
                         ) -> tuple[list[dict], bool]:
    """Scan the LIVE DOM for every element flagged required (the
    ``required`` attribute or ``aria-required="true"``), deduped by
    resolved label — top document AND every same-origin-accessible iframe
    (BLOCKER fix: this used to be top-document-only, which meant a
    required custom question rendered ONLY inside a company's embedded
    ATS iframe was never discovered at all — not "seen and thought
    answered," genuinely invisible to this scan, so ``required_empty``
    stayed clean and the row went to ``awaiting_human_submit`` with a
    real required question never even considered).

    This is what lets the verification pass build the required-set from
    the form itself instead of the ~5 hardcoded ``field_maps.yml`` labels
    — custom / role-specific questions the ATS marks required show up here
    even though the declarative field map never heard of them.

    Returns ``(entries, scan_incomplete)``:
      - ``entries`` — ``[{"label": str, "selectors": [selector], "kind":
        "value" | "checked"}, ...]``, same shape as before this fix (see
        :func:`_resolve_dom_kind` / :func:`dom_field_has_value` for what
        ``kind`` controls).
      - ``scan_incomplete`` — ``True`` iff at least one iframe was known to
        exist (the outer ``page.locator("iframe").count()`` call
        succeeded) but this function could not get that specific frame's
        own root or enumerate its required elements (a cross-origin
        restriction, a detached iframe, or anything else raising). A frame
        that scans cleanly and simply has zero required elements does NOT
        set this. The caller (``run_field_map_fill``) MUST treat
        ``scan_incomplete is True`` as "this scan cannot be trusted as a
        clean required-set" and refuse to report the attempt as clean —
        silently degrading to "found nothing in there" is exactly the bug
        this fix closes.

    Exception handling, deliberately asymmetric between the two document
    scopes:
      - Top document: unchanged best-effort contract from before this fix
        — a single element's read failing is skipped, and the top-level
        ``[required], [aria-required="true"]`` query itself raising
        degrades to "no top-document required fields found" (NOT
        ``scan_incomplete`` — this mirrors the pre-existing top-document
        degrade-to-empty behavior, unchanged by this fix).
      - Per iframe: getting the frame's own root
        (``page.frame_locator("iframe").nth(i)``) or enumerating its
        required elements (``.locator(selector).count()``) raising IS a
        ``scan_incomplete`` event — we now know a frame exists and could
        not confirm whether it holds any required, unanswered field. An
        individual element's attribute read failing once we're already
        enumerating a successfully-counted frame is still just skipped
        (same per-element best-effort as the top document), not itself
        incomplete.
      - The OUTER ``page.locator("iframe").count()`` call raising (e.g. a
        stub Page with no iframe support at all, as used throughout the
        pre-frame-aware unit tests) is treated as "zero iframes on this
        page," NOT incomplete — there is a real difference between "this
        page object can't tell me about frames" (nothing to distrust) and
        "there IS a frame here and I can't see into it" (untrustworthy).
    """
    log = log or logger
    selector = '[required], [aria-required="true"]'
    found: dict[str, tuple[str, str]] = {}

    try:
        top_count = page.locator(selector).count()
    except Exception:
        top_count = 0
    else:
        for i in range(top_count):
            try:
                el = page.locator(selector).nth(i)
                label = _resolve_dom_label(page, el)
                el_selector = _resolve_dom_selector(el)
                kind = _resolve_dom_kind(el)
            except Exception:
                continue
            if label and label not in found:
                found[label] = (el_selector, kind)

    scan_incomplete = False
    try:
        frame_count = page.locator("iframe").count()
    except Exception:
        frame_count = 0

    for i in range(frame_count):
        try:
            frame_root = page.frame_locator("iframe").nth(i)
            frame_required_count = frame_root.locator(selector).count()
        except Exception:
            scan_incomplete = True
            continue
        for j in range(frame_required_count):
            try:
                el = frame_root.locator(selector).nth(j)
                label = _resolve_dom_label(page, el, label_root=frame_root)
                el_selector = _resolve_dom_selector(el)
                kind = _resolve_dom_kind(el)
            except Exception:
                continue
            if label and label not in found:
                found[label] = (el_selector, kind)

    entries = [
        {"label": label, "selectors": [sel] if sel else [], "kind": kind}
        for label, (sel, kind) in found.items()
    ]
    return entries, scan_incomplete


def dom_field_has_value(page: Any, entry: dict, *,
                        log: logging.Logger | None = None) -> bool:
    """Does the DOM-discovered required field (a ``scan_required_fields``
    entry) already have a value on the live page?

    Branches on ``entry["kind"]`` (see :func:`_resolve_dom_kind`):
      - ``"checked"`` — a radio/checkbox group; true iff ``{selector}:checked``
        matches at least one element (native CSS pseudo-class, not a
        Playwright extension — works identically to any other selector
        here). ``count()`` on a selector with no match returns 0
        immediately, no actionability wait, so this is never a hang risk.
      - ``"value"`` (default, back-compat for any caller not setting
        ``kind``) — the existing ``read_value`` re-read.
    """
    log = log or logger
    selectors = entry.get("selectors") or []
    if not selectors:
        return False
    if entry.get("kind") == "checked":
        for selector in selectors:
            try:
                if page.locator(f"{selector}:checked").count() > 0:
                    return True
            except Exception:
                continue
            for frame_loc_page in _iter_frame_locators(page, f"{selector}:checked"):
                try:
                    if frame_loc_page.count() > 0:
                        return True
                except Exception:
                    continue
        return False
    return bool(read_value(page, selectors, log=log))


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
