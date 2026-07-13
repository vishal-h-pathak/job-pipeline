"""prepare_dom/field_maps.py — declarative per-ATS field maps (Part A / #3).

Each deterministic ATS filler (``greenhouse`` / ``lever`` / ``ashby``) used to
hardcode its fill sequence in Python, so every ATS form change meant a code
change. This module moves the field *definitions* into data
(``field_maps.yml``) and provides one generic filler — ``apply_field_map`` —
that dispatches each spec to the right ``_common`` primitive. Fixing or adding
an ATS is now an edit to the YAML, not an engineering cycle.

Spec schema (one entry per field, ordered — fill order is list order):

    key        profile key into the ``values`` dict passed to apply_field_map
               (a ``build_field_map`` label like "First Name", or the special
               "__resume__" / "__cover_letter__" source keys).
    label      human label, used for the label_selectors fallback and for the
               filled / required-empty report. Defaults to ``key``.
    name       canonical input ``name`` attribute, if the ATS has one. Builds
               ``input[name="..."]`` + ``textarea[name="..."]`` selectors
               (the old ``name_attr_selectors`` pair).
    type       text | file | textarea | select | combobox. Default "text".
               ``combobox`` is a react-select / ``role="combobox"`` div-based
               field (Task 2) — its selector chain is built the same way as
               file/textarea/select (explicit ``selectors`` + name pair,
               no label-selector fallback, since ``label_selectors`` only
               ever targets real ``<input>`` elements).
    required   bool. Required fields left empty (no value, or a value that no
               selector matched) are reported in ``required_empty``.
    selectors  explicit ordered selector lead. For text it goes BEFORE the
               label fallbacks (phone's intl-tel-input anchor); for
               file/textarea/select it is the WHOLE chain (no label fallback).
    fuzzy_name_fallback
               text-only. Append the two ``input[name*="..."]`` fuzzy
               selectors (Ashby has no name map and relies on these). A
               per-ATS ``defaults:`` block can set it once for every field.

Selector-chain construction is parity-exact with the pre-rewrite adapters —
see the per-branch comments in ``_selectors_for``.

The YAML loader mirrors ``jobpipe/hunt/sources/_portals.py``: cached at module
level, ``Path(__file__).with_name(...)`` resolution, and a graceful empty
return (never raise) when the file or an ATS entry is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ._common import (
    build_field_map,
    dom_field_has_value,
    fill_text,
    label_selectors,
    load_cover_letter,
    note_unfilled_custom_questions,
    paste_textarea,
    read_value,
    scan_required_fields,
    select_combobox,
    select_option,
    upload_file,
)

logger = logging.getLogger("prepare_dom.field_maps")

_FIELD_MAPS_PATH = Path(__file__).with_name("field_maps.yml")
_FIELD_MAPS_CACHE: Optional[dict] = None


def _load_all() -> dict:
    """Parse ``field_maps.yml`` once and cache it. Returns {} on any problem
    (missing file, no PyYAML, parse error) so a config issue degrades the
    deterministic tier to "fills nothing" rather than crashing the run."""
    global _FIELD_MAPS_CACHE
    if _FIELD_MAPS_CACHE is not None:
        return _FIELD_MAPS_CACHE
    if not _FIELD_MAPS_PATH.exists():
        logger.warning("field_maps.yml not found at %s", _FIELD_MAPS_PATH)
        _FIELD_MAPS_CACHE = {}
        return _FIELD_MAPS_CACHE
    try:
        import yaml  # PyYAML — declared dep (pyproject.toml)
    except ImportError:
        logger.warning("PyYAML not installed; cannot read field_maps.yml")
        _FIELD_MAPS_CACHE = {}
        return _FIELD_MAPS_CACHE
    try:
        _FIELD_MAPS_CACHE = yaml.safe_load(_FIELD_MAPS_PATH.read_text()) or {}
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        logger.error("field_maps.yml parse failed: %s", exc)
        _FIELD_MAPS_CACHE = {}
    return _FIELD_MAPS_CACHE


def load_field_map(ats: str) -> list[dict]:
    """Return the ordered field specs for ``ats`` (greenhouse / lever / ashby).

    Per-ATS ``defaults`` are shallow-merged into every field spec (the spec's
    own keys win), so e.g. Ashby can set ``fuzzy_name_fallback: true`` once.
    Unknown ATS → ``[]`` (the adapter then fills nothing and reports it).
    """
    entry = _load_all().get(ats)
    if not entry:
        return []
    defaults = entry.get("defaults") or {}
    fields = entry.get("fields") or []
    merged: list[dict] = []
    for spec in fields:
        merged.append({**defaults, **spec})
    return merged


def _selectors_for(spec: dict, label: str, ftype: str) -> list[str]:
    """Build the ordered selector chain for one spec — parity-exact with the
    pre-rewrite per-ATS adapters.

      - explicit ``selectors`` lead (phone's ``input[type=tel]:visible``
        anchor, or the whole file/textarea list);
      - then the ``name`` attr pair (``input[name=]`` / ``textarea[name=]``),
        the old ``name_attr_selectors``;
      - for TEXT only: the four ``label_selectors`` fallbacks, then (if
        flagged) the two ``input[name*=...]`` fuzzy selectors (old
        ``_ashby_field_selectors``). file / textarea / select / combobox use
        the explicit list (+ name pair) ONLY — they never fell back to label
        selectors (``label_selectors`` only ever targets real ``<input>``
        elements, which is meaningless for a div-based combobox container).
    """
    chain: list[str] = list(spec.get("selectors") or [])
    name = spec.get("name")
    if name:
        chain += [f'input[name="{name}"]', f'textarea[name="{name}"]']
    if ftype == "text":
        chain += label_selectors(label)
        if spec.get("fuzzy_name_fallback"):
            low = label.lower()
            chain += [
                f'input[name*="{low.replace(" ", "_")}"]',
                f'input[name*="{low.replace(" ", "")}"]',
            ]
    return chain


_COVER_LETTER_LABEL_NEEDLES = ("cover", "letter")


def _matched_selector_from(misses: list[dict]) -> Optional[str]:
    """Recover the selector a primitive actually matched from its raw
    ``misses`` log (Task 3 / #1). Every primitive appends exactly one
    entry per attempted candidate — failures carry an ``"error"`` key,
    and the SINGLE success (if any) is the last entry appended before the
    primitive returns, carrying no ``"error"`` key. So: the last entry in
    the list, if it lacks ``"error"``, is the match; anything else (list
    empty, or the last entry is itself a failure) means nothing matched."""
    if misses and "error" not in misses[-1]:
        return misses[-1]["selector"]
    return None


def apply_field_map(
    page: Any,
    field_specs: list[dict],
    values: dict,
    *,
    log: logging.Logger | None = None,
) -> dict:
    """Fill ``page`` from an ordered list of declarative field specs.

    Dispatches each spec to the matching ``_common`` primitive
    (``fill_text`` / ``upload_file`` / ``paste_textarea`` / ``select_option``
    / ``select_combobox``) using the value at ``values[spec["key"]]``. Specs
    whose value is empty are skipped (mirrors the old ``if not value:
    continue``).

    Returns ``{"filled": [labels], "required_empty": [labels], "fill_report":
    [...]}``:
      - ``filled``  — labels whose primitive returned True AND whose value
        stuck on a DOM re-read (honest fill verification, #2 — a fill that
        matched a selector but didn't stick, e.g. a React form discarding
        ``.fill()``, no longer counts as filled). File uploads don't get a
        DOM value re-read (Playwright can't read an ``input[type=file]``'s
        value back) — instead ``upload_file`` runs its own upload-completion
        wait (Task 2) and a field only counts as filled once that confirms
        the ATS actually accepted the file, not merely that
        ``set_input_files`` didn't raise. Comboboxes don't get a re-read
        either — ``select_combobox`` already verifies its own selection
        stuck by reading back the container's display text before returning
        True.
      - ``required_empty`` — labels of ``required`` specs that ended up empty
        on the form (no value, no matching selector, or a fill that didn't
        stick). This is what the verification pass (#4 / Part B) reads.
      - ``fill_report`` — ONE entry per spec, whether or not it had a value
        to try (Task 3 / #1 — the old ``required_empty`` list only ever
        recorded required misses; an optional field that silently failed
        left no trace anywhere). Each entry:
        ``{"key", "label", "type", "required", "attempted",
        "matched_selector", "value_verified", "misses"}`` — ``misses`` is
        the per-selector failure log (``[{"selector", "error"}, ...]``),
        filtered down from the primitive's raw candidate log (which also
        carries the one success entry, if any — see
        ``_matched_selector_from``).
    """
    log = log or logger
    filled: list[str] = []
    required_empty: list[str] = []
    fill_report: list[dict] = []

    for spec in field_specs:
        key = spec.get("key")
        label = spec.get("label") or key
        ftype = spec.get("type", "text")
        required = bool(spec.get("required"))
        value = values.get(key) or ""
        attempted = bool(value)

        if not attempted:
            if required:
                required_empty.append(label)
            fill_report.append({
                "key": key, "label": label, "type": ftype,
                "required": required, "attempted": False,
                "matched_selector": None, "value_verified": False,
                "misses": [],
            })
            continue

        chain = _selectors_for(spec, label, ftype)
        candidates: list[dict] = []

        if ftype == "file":
            # ``matched`` (selector found + set_input_files didn't raise) is
            # NOT sufficient — only an upload the completion wait actually
            # confirmed counts as filled (Task 2 honesty fix).
            _matched, confirmed = upload_file(
                page, chain, value, log=log, misses=candidates,
            )
            ok = confirmed
        elif ftype == "combobox":
            ok = select_combobox(page, chain, value, log=log, misses=candidates)
        elif ftype == "textarea":
            # Scope the bare catch-all "textarea" selector to the
            # cover-letter spec only (Task 3 / #3) — no other textarea spec
            # exists in field_maps.yml today, but this keeps the scoping
            # tied to the cover-letter concept rather than to "any
            # textarea", which is what the requirement actually asks for.
            scoped_needles = (
                _COVER_LETTER_LABEL_NEEDLES if key == "__cover_letter__" else None
            )
            ok = paste_textarea(
                page, chain, value, log=log, misses=candidates,
                scoped_needles=scoped_needles,
            )
            if ok:
                # The re-read must use the SAME scoped enumeration the fill
                # used (Task 3 finding fix) — a plain .first re-read would
                # check the wrong textarea whenever the fill landed on a
                # non-first candidate, wrongly demoting a fill that stuck.
                ok = bool(read_value(
                    page, chain, log=log, scoped_needles=scoped_needles,
                ))
        elif ftype == "select":
            ok = select_option(page, chain, value, log=log, misses=candidates)
            if ok:
                ok = bool(read_value(page, chain, log=log))
        else:  # text (default)
            ok = fill_text(page, chain, value, log=log, misses=candidates)
            if ok:
                ok = bool(read_value(page, chain, log=log))

        matched_selector = _matched_selector_from(candidates)
        real_misses = [m for m in candidates if "error" in m]
        fill_report.append({
            "key": key, "label": label, "type": ftype,
            "required": required, "attempted": True,
            "matched_selector": matched_selector,
            "value_verified": bool(ok),
            "misses": real_misses,
        })

        if ok:
            filled.append(label)
        elif required:
            required_empty.append(label)

    return {
        "filled": filled, "required_empty": required_empty,
        "fill_report": fill_report,
    }


def run_field_map_fill(
    applicant,
    page,
    job: dict,
    ats: str,
    *,
    screenshot_label: str,
    resume_path: Optional[str] = None,
    cover_letter_path: Optional[str] = None,
    value_overrides: Optional[dict] = None,
    note_custom_questions: bool = False,
    readiness_timeout: bool = False,
    log: logging.Logger | None = None,
) -> dict:
    """The shared body the per-ATS adapters call after their own navigation /
    readiness wait. Builds the value map (M-1 ``form_answers`` + resume /
    cover-letter sources), runs ``apply_field_map``, assembles the same
    operator notes the pre-rewrite adapters produced, screenshots, and returns
    the adapter's ``{success, screenshot_path, notes, fields_filled,
    required_empty, fill_report, readiness_timeout}`` dict.

    ``value_overrides`` lets Lever force the full name into the Name / Full
    Name keys; ``note_custom_questions`` toggles the "N role-specific
    question(s) NOT auto-filled" note (Greenhouse / Lever emit it, Ashby did
    not). ``success`` is the honest cleanliness gate (#2): the DOM's own
    required-set (YAML-declared fields UNION whatever ``scan_required_fields``
    finds live on the page — top document AND every same-origin-accessible
    iframe, custom/role-specific questions included) has zero entries left
    empty. Any required gap — YAML or DOM-only — routes to the
    assisted-manual hand-off instead of ``awaiting_human_submit``. When
    ``scan_required_fields`` reports it could not fully scan an iframe it
    knew existed (``scan_incomplete``), ``success`` is forced ``False``
    unconditionally (BLOCKER fix) — an inaccessible frame must never be
    misread as "nothing required in there."

    ``readiness_timeout`` (Task 3 / #4) is threaded straight through from the
    caller's own ``_common.wait_for_form_ready`` call — it did NOT block the
    fill (the adapter proceeds either way), but a caller that never saw
    positive form evidence should be distinguishable in the attempt notes
    from a fill that ran cleanly against a confirmed-present form.
    """
    log = log or logger
    notes_parts: list[str] = []

    specs = load_field_map(ats)
    values = build_field_map(job)
    if value_overrides:
        values.update(value_overrides)

    resume_ok = bool(resume_path and Path(resume_path).exists())
    if resume_ok:
        values["__resume__"] = resume_path
    cover_text = load_cover_letter(cover_letter_path) if cover_letter_path else ""
    if cover_text:
        values["__cover_letter__"] = cover_text

    result = apply_field_map(page, specs, values, log=log)
    filled = result["filled"]
    required_empty = result["required_empty"]
    std_filled = [f for f in filled if f not in ("Resume", "Cover Letter")]

    notes_parts.append(
        f"Filled fields: {', '.join(std_filled) if std_filled else 'none'}"
    )

    if "Resume" in filled:
        notes_parts.append(f"Uploaded resume: {Path(resume_path).name}")
    elif resume_path and not resume_ok:
        notes_parts.append(f"Resume path not found: {resume_path}")
    elif resume_path:
        notes_parts.append("Resume upload: no file input found")

    if cover_text and "Cover Letter" in filled:
        notes_parts.append("Pasted cover letter")
    elif cover_text:
        notes_parts.append("Cover letter: no textarea found")

    if note_custom_questions:
        note_unfilled_custom_questions(job, notes_parts)

    # ── Form-derived required-set (honest verification, #2) ──────────────
    # The YAML map only declares ~5 canonical identity fields required;
    # scan the LIVE DOM for anything else the form itself flags required
    # (aria-required, custom/role-specific questions) so "filled 5 of 5"
    # can't render next to 10 unanswered required questions the field map
    # never heard of.
    yaml_required_labels = [
        s.get("label") or s.get("key") for s in specs if s.get("required")
    ]
    dom_required, scan_incomplete = scan_required_fields(page, log=log)
    tracked = set(yaml_required_labels)
    all_required_labels = list(yaml_required_labels)
    for entry in dom_required:
        label = entry["label"]
        if label in tracked:
            continue
        tracked.add(label)
        all_required_labels.append(label)
        has_value = dom_field_has_value(page, entry, log=log)
        if not has_value and label not in required_empty:
            required_empty.append(label)

    # BLOCKER fix: an iframe scan_required_fields could not fully scan (a
    # cross-origin restriction, a detached frame, whatever) must NEVER be
    # read as "nothing required in there" — that is exactly the false-clean
    # result the source review flagged. Inject a distinguishable sentinel
    # label into the required-set so this attempt can never look clean and
    # so the handoff notes tell the human WHY, instead of looking like a
    # real form field silently went unanswered.
    if scan_incomplete:
        incomplete_label = "Unscannable embedded content — verify required fields manually"
        if incomplete_label not in tracked:
            tracked.add(incomplete_label)
            all_required_labels.append(incomplete_label)
        if incomplete_label not in required_empty:
            required_empty.append(incomplete_label)

    screenshot_path = applicant.take_screenshot(page, label=screenshot_label)
    notes_parts.append(f"Screenshot: {screenshot_path}")

    # ``required_empty`` already carries the incomplete-scan sentinel above
    # when applicable, which alone forces this False — the explicit
    # ``and not scan_incomplete`` is a belt-and-suspenders guarantee that
    # holds even if some future change ever cleared required_empty after
    # this point without knowing about the sentinel.
    success = len(required_empty) == 0 and not scan_incomplete

    return {
        "success": success,
        "screenshot_path": screenshot_path,
        "notes": "\n".join(notes_parts),
        "fields_filled": std_filled,
        "required_empty": required_empty,
        "required_total": len(all_required_labels),
        "fill_report": result.get("fill_report"),
        "readiness_timeout": readiness_timeout,
    }
