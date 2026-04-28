"""scripts/cv_sync_check.py — CV / article-digest / BASE_RESUME drift detector (J-9).

Compares quantitative claims across the four sources of truth:

  - `../job-hunter/profile/cv.md`              — master CV (markdown)
  - `../job-hunter/profile/article-digest.md`  — proof-point digest
  - `tailor/latex_resume.py::BASE_RESUME`      — structured LaTeX data
  - `../job-hunter/CLAUDE.md`                  — narrative aggregator

Reports:
  - Anchored claims (employee number, neuron count, years-of-experience,
    project periods) where the number differs between sources.
  - Anchors that appear in some sources but not others.

Designed to be:
  - Standalone: `python -m scripts.cv_sync_check` from job-applicant
  - Importable: `tailor_resume` calls `warn_if_drift()` once per
    session (cached) so a warning shows up before any LLM call goes
    out — never blocks tailoring.

The detector is anchor-based on purpose. Free-form regex over four
files explodes in noise; the high-value comparisons are a small fixed
set of facts that recurr in all sources (employee #5, 40 LIF neurons,
period at GTRI, period at Rain, etc.).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

_REPO_ROOT = Path(__file__).parent.parent  # job-applicant/
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("cv_sync_check")

# Resolution mirrors prompts.load_profile() — canonical lives in sibling
# job-hunter repo.
_HUNTER_ROOT = _REPO_ROOT.parent / "job-hunter"


# ── Anchored claims ──────────────────────────────────────────────────────
#
# Each anchor pulls a NUMBER preceded or followed by a tiny vocabulary
# of clue words. Multiple regexes per anchor — sources spell things
# differently (employee #5 vs employee 5 vs "fifth employee"). The
# detector reports the number string, not the regex; cross-source
# disagreement is what we flag.

ANCHORS: dict[str, list[re.Pattern]] = {
    "rain_employee_number": [
        re.compile(r"employee\s*#?\s*(\d+)", re.IGNORECASE),
        re.compile(r"(\d+)(?:st|nd|rd|th)\s+employee", re.IGNORECASE),
    ],
    "rain_lif_neuron_count": [
        re.compile(r"(\d+)\s+(?:leaky integrate[- ]and[- ]fire|LIF)\s+neuron", re.IGNORECASE),
        re.compile(r"PCB[^.]*?(\d+)\s+(?:LIF|leaky)", re.IGNORECASE),
    ],
    "age_at_rain": [
        # Require "Rain" within ~30 chars so we don't catch "at 94% mAP"
        # or "at 19 employees" out of unrelated context.
        re.compile(
            r"(?:Rain[\w ]{0,30}?\bat\s+(\d{1,2})|\bat\s+(\d{1,2})\b[\w ]{0,30}?Rain)",
            re.IGNORECASE,
        ),
    ],
    "years_at_gtri": [
        re.compile(r"(\d+)(?:\s*\+)?\s*years?\s+at\s+GTRI", re.IGNORECASE),
        re.compile(r"(\d+)(?:\s*\+)?\s*years?\s+working\s+at\s+GTRI", re.IGNORECASE),
        re.compile(r"~?(\d+)(?:\s*\+)?\s*years?\s+at\s+GTRI", re.IGNORECASE),
    ],
    "weather_stations_count": [
        re.compile(r"(\d+)\s+weather\s+stations?", re.IGNORECASE),
    ],
    "anemometers_count": [
        re.compile(r"(\d+)\s+anemometers?", re.IGNORECASE),
    ],
}


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _claims_for(text: str) -> dict[str, set[str]]:
    """For each anchor, collect every number this source asserts.

    Patterns may have multiple alternations with separate capture
    groups (e.g. "at N" near Rain in either order). We pull the first
    non-None group for each match so the caller doesn't need to know
    which branch hit.
    """
    out: dict[str, set[str]] = {}
    for anchor, patterns in ANCHORS.items():
        seen: set[str] = set()
        for pat in patterns:
            for m in pat.finditer(text):
                for g in m.groups():
                    if g:
                        seen.add(g)
                        break
        if seen:
            out[anchor] = seen
    return out


def _gather_sources() -> dict[str, str]:
    """Read all four sources into a {name: text} dict."""
    sources: dict[str, str] = {}
    sources["profile/cv.md"] = _read(_HUNTER_ROOT / "profile" / "cv.md")
    sources["profile/article-digest.md"] = _read(
        _HUNTER_ROOT / "profile" / "article-digest.md"
    )
    sources["job-hunter/CLAUDE.md"] = _read(_HUNTER_ROOT / "CLAUDE.md")

    # BASE_RESUME — import the dict + serialize so the regex can match.
    try:
        from tailor.latex_resume import BASE_RESUME  # noqa: WPS433

        sources["tailor/latex_resume.py::BASE_RESUME"] = json.dumps(
            BASE_RESUME, indent=2
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("could not import BASE_RESUME: %s", exc)
        sources["tailor/latex_resume.py::BASE_RESUME"] = ""

    return sources


def detect_drift() -> dict:
    """Run all detectors and return a structured report."""
    sources = _gather_sources()
    by_source: dict[str, dict[str, set[str]]] = {
        name: _claims_for(text) for name, text in sources.items()
    }

    report = {"anchors": {}, "drift": [], "missing": []}

    for anchor in ANCHORS:
        per_source: dict[str, list[str]] = {}
        for src, claims in by_source.items():
            if anchor in claims:
                per_source[src] = sorted(claims[anchor])
        report["anchors"][anchor] = per_source

        if not per_source:
            continue

        # Cross-source consistency: collapse to the unique value set per
        # source. If sources disagree, flag.
        value_sets = {tuple(v) for v in per_source.values()}
        if len(value_sets) > 1:
            report["drift"].append(
                {
                    "anchor": anchor,
                    "by_source": per_source,
                }
            )

        # Missing-source check: which sources don't mention this anchor
        # at all? Surfaced separately so it doesn't double-fire alongside
        # disagreement.
        all_sources = set(sources.keys())
        seen_sources = set(per_source.keys())
        missing = sorted(all_sources - seen_sources)
        if missing and len(per_source) >= 2:
            # Only flag missing when AT LEAST two sources agree on a
            # value — otherwise "missing" is just noise on an anchor
            # nobody uses.
            if len(value_sets) == 1:
                report["missing"].append(
                    {
                        "anchor": anchor,
                        "value": next(iter(value_sets))[0]
                        if next(iter(value_sets))
                        else None,
                        "missing_from": missing,
                    }
                )

    return report


def render_markdown(report: dict) -> str:
    lines = [
        f"# CV-Sync Drift Report — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        "Anchor-based comparison across `cv.md`, `article-digest.md`,",
        "`BASE_RESUME`, and `CLAUDE.md`. A drift entry means two sources",
        "assert different numbers for the same fact. A missing entry means",
        "a fact appears in some sources but not others.",
        "",
    ]
    if report["drift"]:
        lines.append("## Drift (numbers disagree)")
        lines.append("")
        for d in report["drift"]:
            lines.append(f"### `{d['anchor']}`")
            for src, vals in d["by_source"].items():
                lines.append(f"- **{src}** → `{', '.join(vals)}`")
            lines.append("")
    else:
        lines.append("_No numeric drift detected._")
        lines.append("")

    if report["missing"]:
        lines.append("## Missing (fact in some sources, not others)")
        lines.append("")
        for m in report["missing"]:
            value = m["value"] or "(value)"
            lines.append(
                f"- `{m['anchor']}` = `{value}` is missing from: "
                + ", ".join(f"`{s}`" for s in m["missing_from"])
            )
        lines.append("")

    lines.append("## All anchored values, by source")
    lines.append("")
    for anchor, per_source in report["anchors"].items():
        if not per_source:
            continue
        lines.append(f"### `{anchor}`")
        for src, vals in per_source.items():
            lines.append(f"- {src}: `{', '.join(vals)}`")
        lines.append("")

    return "\n".join(lines)


# ── Public API for warn-on-drift use from tailor_resume.py ──────────────
_WARN_CACHE: Optional[bool] = None


def warn_if_drift() -> bool:
    """Run the detector once per process; emit a warning if drift found.

    Idempotent — caches the result so repeated calls cost nothing. Never
    blocks; tailoring proceeds even if drift is present. Returns True
    if drift was detected.
    """
    global _WARN_CACHE
    if _WARN_CACHE is not None:
        return _WARN_CACHE
    try:
        report = detect_drift()
    except Exception as exc:  # pragma: no cover
        logger.debug("cv-sync check skipped: %s", exc)
        _WARN_CACHE = False
        return False
    has_drift = bool(report["drift"])
    if has_drift:
        # Single-line warning; the operator can run the script for full detail.
        anchors = ", ".join(d["anchor"] for d in report["drift"])
        logger.warning(
            "CV-sync drift detected on anchors: %s — run "
            "`python -m scripts.cv_sync_check` for full report",
            anchors,
        )
    _WARN_CACHE = has_drift
    return has_drift


def main() -> None:
    parser = argparse.ArgumentParser(description="CV-sync drift detector (J-9)")
    parser.add_argument(
        "--out",
        default=None,
        help="Write the markdown report to this path (default: print to stdout).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    report = detect_drift()
    md = render_markdown(report)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        logger.info("wrote drift report to %s", args.out)
    else:
        print(md)

    if report["drift"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
