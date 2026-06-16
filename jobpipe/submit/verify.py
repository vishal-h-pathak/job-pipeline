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
    empty on the form. ``required`` is the field map's required count, or
    ``None`` when there is no deterministic map for the ATS.
    """
    req = _required_labels(ats)
    still = list(result.get("required_empty") or [])
    if not still:
        # Universal/agent path carries no required_empty; fall back to the
        # agent's own flagged-uncertain fields.
        still = list(result.get("uncertain_fields") or [])

    if req:
        required = len(req)
        # ``required_empty`` entries are required labels; keep only those that
        # actually belong to the required set (defensive against agent hints
        # bleeding in), but never let filtering hide a real gap.
        missing = [s for s in still if s in req] or still
        filled = max(0, required - len(missing))
        still_needs = missing
        summary = f"filled {filled} of {required} required field(s)"
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
