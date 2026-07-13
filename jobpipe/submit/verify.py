"""verify.py — post-fill verification summary (Part B / #4).

After an adapter finishes pre-filling a form (cleanly OR degrading to an
assisted-manual hand-off), turn its result into a one-line, human-readable
summary the cockpit renders next to the "Submitted ✓ → Next" button:

    filled 4 of 5 required field(s); still needs: Phone

For the deterministic adapters (greenhouse / lever / ashby) the required set
and the still-empty set come straight from the declarative field map plus the
adapter's ``required_empty`` (Part A). For the universal agent path there is
no field map — the summary degrades to whatever the agent flagged
(``uncertain_fields``) and reports no fixed denominator.

This module is pure (no DB / browser) so it is trivially unit-tested; the
pipeline does the screenshot + row write around it.
"""

from __future__ import annotations

from jobpipe.submit.adapters.prepare_dom.field_maps import load_field_map


def _required_labels(ats: str) -> list[str]:
    """Labels of the ``required: true`` specs in this ATS's field map (the
    label, falling back to the key). Empty for ATSes with no field map."""
    return [
        s.get("label") or s.get("key")
        for s in load_field_map(ats)
        if s.get("required")
    ]


def build_prefill_verification(result: dict, ats: str) -> dict:
    """Build ``{filled, required, still_needs, summary}`` from a fill result.

    ``result`` is the adapter return dict — ``required_empty`` (deterministic
    adapters) or ``uncertain_fields`` (agent fallback) name the fields still
    empty on the form. The denominator prefers ``result["required_total"]``
    (P0 #2 — ``run_field_map_fill``'s form-derived required-set: the YAML
    map's required labels UNION whatever ``scan_required_fields`` found live
    on the page, custom/role-specific questions included) and falls back to
    the static YAML-only count for callers that don't set it. ``required`` is
    ``None`` when there is no deterministic map for the ATS at all.

    Unanswered custom/role-specific questions (``still_needs`` entries the
    YAML map never declared required) are surfaced as their own clause —
    "N required custom question(s) unanswered" — instead of buried in the
    same comma list as missing standard fields, so a form with 5/5 standard
    fields filled but 3 unanswered custom questions doesn't read as clean.
    """
    yaml_required = _required_labels(ats)
    still = list(result.get("required_empty") or [])
    if not still:
        # Universal/agent path carries no required_empty; fall back to the
        # agent's own flagged-uncertain fields.
        still = list(result.get("uncertain_fields") or [])

    required = result.get("required_total")
    if required is None and yaml_required:
        required = len(yaml_required)

    if required:
        filled = max(0, required - len(still))
        still_needs = still
        summary = f"filled {filled} of {required} required field(s)"
        if still_needs:
            yaml_set = set(yaml_required)
            standard_missing = [s for s in still_needs if s in yaml_set]
            custom_missing = [s for s in still_needs if s not in yaml_set]
            clauses = []
            if standard_missing:
                clauses.append("still needs: " + ", ".join(standard_missing))
            if custom_missing:
                n = len(custom_missing)
                clauses.append(
                    f"{n} required custom question"
                    f"{'s' if n != 1 else ''} unanswered"
                )
            summary += "; " + "; ".join(clauses)
        else:
            summary += "; all required fields present"
    else:
        required = None
        filled = None
        still_needs = still
        summary = "prepared (no deterministic field map for this ATS)"
        if still_needs:
            summary += "; still needs: " + ", ".join(still_needs)
        else:
            summary += "; all required fields present"

    return {
        "filled": filled,
        "required": required,
        "still_needs": still_needs,
        "summary": summary,
    }
