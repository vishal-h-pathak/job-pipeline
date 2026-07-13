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
    type       text | file | textarea | select. Default "text".
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
    fill_text,
    label_selectors,
    load_cover_letter,
    note_unfilled_custom_questions,
    paste_textarea,
    read_value,
    scan_required_fields,
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
        ``_ashby_field_selectors``). file / textarea / select use the explicit
        list (+ name pair) ONLY — they never fell back to label selectors.
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


def apply_field_map(
    page: Any,
    field_specs: list[dict],
    values: dict,
    *,
    log: logging.Logger | None = None,
) -> dict:
    """Fill ``page`` from an ordered list of declarative field specs.

    Dispatches each spec to the matching ``_common`` primitive
    (``fill_text`` / ``upload_file`` / ``paste_textarea`` / ``select_option``)
    using the value at ``values[spec["key"]]``. Specs whose value is empty are
    skipped (mirrors the old ``if not value: continue``).

    Returns ``{"filled": [labels], "required_empty": [labels]}``:
      - ``filled``  — labels whose primitive returned True AND whose value
        stuck on a DOM re-read (honest fill verification, #2 — a fill that
        matched a selector but didn't stick, e.g. a React form discarding
        ``.fill()``, no longer counts as filled). File uploads are exempt:
        Playwright can't read an ``input[type=file]``'s value back, so they
        keep the selector-matched contract ``upload_file`` already applies.
      - ``required_empty`` — labels of ``required`` specs that ended up empty
        on the form (no value, no matching selector, or a fill that didn't
        stick). This is what the verification pass (#4 / Part B) reads.
    """
    log = log or logger
    filled: list[str] = []
    required_empty: list[str] = []

    for spec in field_specs:
        key = spec.get("key")
        label = spec.get("label") or key
        ftype = spec.get("type", "text")
        required = bool(spec.get("required"))
        value = values.get(key) or ""

        if not value:
            if required:
                required_empty.append(label)
            continue

        chain = _selectors_for(spec, label, ftype)
        if ftype == "file":
            ok = upload_file(page, chain, value, log=log)
        elif ftype == "textarea":
            ok = paste_textarea(page, chain, value, log=log)
            if ok:
                ok = bool(read_value(page, chain, log=log))
        elif ftype == "select":
            ok = select_option(page, chain, value, log=log)
            if ok:
                ok = bool(read_value(page, chain, log=log))
        else:  # text (default)
            ok = fill_text(page, chain, value, log=log)
            if ok:
                ok = bool(read_value(page, chain, log=log))

        if ok:
            filled.append(label)
        elif required:
            required_empty.append(label)

    return {"filled": filled, "required_empty": required_empty}


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
    log: logging.Logger | None = None,
) -> dict:
    """The shared body the per-ATS adapters call after their own navigation /
    load-state waits. Builds the value map (M-1 ``form_answers`` + resume /
    cover-letter sources), runs ``apply_field_map``, assembles the same
    operator notes the pre-rewrite adapters produced, screenshots, and returns
    the adapter's ``{success, screenshot_path, notes, fields_filled,
    required_empty}`` dict.

    ``value_overrides`` lets Lever force the full name into the Name / Full
    Name keys; ``note_custom_questions`` toggles the "N role-specific
    question(s) NOT auto-filled" note (Greenhouse / Lever emit it, Ashby did
    not). ``success`` is the honest cleanliness gate (#2): the DOM's own
    required-set (YAML-declared fields UNION whatever ``scan_required_fields``
    finds live on the page, custom/role-specific questions included) has
    zero entries left empty. Any required gap — YAML or DOM-only — routes to
    the assisted-manual hand-off instead of ``awaiting_human_submit``.
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
    dom_required = scan_required_fields(page, log=log)
    tracked = set(yaml_required_labels)
    all_required_labels = list(yaml_required_labels)
    for entry in dom_required:
        label = entry["label"]
        if label in tracked:
            continue
        tracked.add(label)
        all_required_labels.append(label)
        value = (
            read_value(page, entry["selectors"], log=log)
            if entry["selectors"] else ""
        )
        if not value and label not in required_empty:
            required_empty.append(label)

    screenshot_path = applicant.take_screenshot(page, label=screenshot_label)
    notes_parts.append(f"Screenshot: {screenshot_path}")

    return {
        "success": len(required_empty) == 0,
        "screenshot_path": screenshot_path,
        "notes": "\n".join(notes_parts),
        "fields_filled": std_filled,
        "required_empty": required_empty,
        "required_total": len(all_required_labels),
    }
