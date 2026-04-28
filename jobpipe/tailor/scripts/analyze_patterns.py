"""scripts/analyze_patterns.py — Closed-loop pattern analysis (J-6).

Pulls every job row from Supabase, groups by configurable dimensions
(archetype, status, company_size, comp_band, ats), computes response /
interview / offer rates per group, and surfaces patterns that pass an
effect-size threshold so the report doesn't drown in noise.

Outputs two artifacts each run:

1. Markdown report under `reports/patterns-{YYYY-MM-DD}.md`
2. JSON row written to the `pattern_analyses` Supabase table
   (consumed by /dashboard/insights).

Designed to run standalone, e.g. weekly via cron:

    cd job-applicant && python -m scripts.analyze_patterns

The cron line itself is the user's responsibility — this script just
needs to be invokable from the repo root with the standard env loaded.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Allow `python -m scripts.analyze_patterns` from the repo root.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from db import client  # noqa: E402

logger = logging.getLogger("analyze_patterns")

# Status buckets that count as "the funnel made progress past application
# submission". These are the rates we surface in the report.
APPLIED_STATUSES = {"applied", "submitted", "submit_confirmed", "ready_to_submit"}
RESPONDED_STATUSES = {"responded", "interview", "interviewing", "offer", "rejected_post_interview"}
INTERVIEW_STATUSES = {"interview", "interviewing", "offer"}
OFFER_STATUSES = {"offer", "accepted"}

# Default group-by dimensions. Any of these can be overridden at the CLI.
DEFAULT_DIMENSIONS = ("archetype", "ats_kind")

# Effect-size threshold: a group's response rate must differ from the
# overall mean by at least this much to be flagged as a pattern. 5pp is
# usually enough to be interesting without drowning the report in noise
# at small N.
DEFAULT_EFFECT_SIZE_PP = 5.0
# Don't report a group if it has fewer than this many rows. Patterns from
# n=2 are noise.
MIN_GROUP_SIZE = 5


@dataclass
class GroupStats:
    name: str
    n: int
    applied: int
    responded: int
    interviewed: int
    offered: int

    @property
    def applied_rate(self) -> float:
        return self.applied / self.n if self.n else 0.0

    @property
    def response_rate(self) -> float:
        return self.responded / self.applied if self.applied else 0.0

    @property
    def interview_rate(self) -> float:
        return self.interviewed / self.applied if self.applied else 0.0

    @property
    def offer_rate(self) -> float:
        return self.offered / self.applied if self.applied else 0.0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "n": self.n,
            "applied": self.applied,
            "responded": self.responded,
            "interviewed": self.interviewed,
            "offered": self.offered,
            "applied_rate": round(self.applied_rate, 4),
            "response_rate": round(self.response_rate, 4),
            "interview_rate": round(self.interview_rate, 4),
            "offer_rate": round(self.offer_rate, 4),
        }


def _bucket_company_size(text: str) -> str:
    """Heuristic. Most jobs don't carry employee count, so this is a
    coarse signal at best — present in the report but never the only
    grouping."""
    if not text:
        return "unknown"
    t = text.lower()
    if any(s in t for s in ("series a", "seed ", "early stage", "founding ")):
        return "early"
    if "series b" in t or "series c" in t:
        return "growth"
    if any(s in t for s in ("public", "ipo'd", "fortune 500", "enterprise")):
        return "large"
    return "unknown"


def _bucket_comp_band(text: str) -> str:
    """Coarse comp band detector for description text. Misses most rows
    (most JDs don't list salary), but the rows it catches are signal."""
    if not text:
        return "unknown"
    import re
    matches = re.findall(r"\$\s?(\d{2,3})(?:[\s,]?000|k\b)", text.lower())
    if not matches:
        return "unknown"
    nums = [int(m) for m in matches]
    high = max(nums)
    if high < 100:
        return "<100k"
    if high < 150:
        return "100-150k"
    if high < 200:
        return "150-200k"
    return "200k+"


def _project_dimensions(job: dict, dimensions: Iterable[str]) -> str:
    """Return a stable joined-key string for the chosen dimensions."""
    parts = []
    for d in dimensions:
        if d == "company_size":
            parts.append(_bucket_company_size(job.get("description") or ""))
        elif d == "comp_band":
            parts.append(_bucket_comp_band(job.get("description") or ""))
        elif d == "ats":
            parts.append((job.get("source") or "unknown").lower())
        else:
            parts.append(str(job.get(d) or "unknown"))
    return " · ".join(parts)


def fetch_jobs() -> list[dict]:
    rows = client.table("jobs").select("*").execute().data or []
    return rows


def aggregate(jobs: list[dict], dimensions: tuple[str, ...]) -> dict[str, GroupStats]:
    buckets: dict[str, dict] = defaultdict(lambda: {
        "n": 0, "applied": 0, "responded": 0, "interviewed": 0, "offered": 0,
    })
    for job in jobs:
        key = _project_dimensions(job, dimensions)
        b = buckets[key]
        b["n"] += 1
        status = (job.get("status") or "").lower()
        if status in APPLIED_STATUSES:
            b["applied"] += 1
        if status in RESPONDED_STATUSES:
            b["responded"] += 1
        if status in INTERVIEW_STATUSES:
            b["interviewed"] += 1
        if status in OFFER_STATUSES:
            b["offered"] += 1
    return {
        k: GroupStats(name=k, n=v["n"], applied=v["applied"],
                      responded=v["responded"], interviewed=v["interviewed"],
                      offered=v["offered"])
        for k, v in buckets.items()
    }


def find_patterns(stats: dict[str, GroupStats], threshold_pp: float) -> list[dict]:
    """Flag groups whose response rate differs from the global mean by
    at least `threshold_pp` percentage points and have at least
    MIN_GROUP_SIZE rows.
    """
    sized = [s for s in stats.values() if s.n >= MIN_GROUP_SIZE and s.applied >= 1]
    if not sized:
        return []
    total_applied = sum(s.applied for s in sized)
    total_responded = sum(s.responded for s in sized)
    overall_rr = (total_responded / total_applied) if total_applied else 0.0
    flagged: list[dict] = []
    for s in sized:
        delta_pp = (s.response_rate - overall_rr) * 100
        if abs(delta_pp) >= threshold_pp:
            direction = "above" if delta_pp > 0 else "below"
            flagged.append({
                "group": s.name,
                "n": s.n,
                "applied": s.applied,
                "response_rate": round(s.response_rate, 4),
                "delta_pp_vs_global": round(delta_pp, 1),
                "direction": direction,
            })
    flagged.sort(key=lambda p: -abs(p["delta_pp_vs_global"]))
    return flagged


def render_markdown(
    stats: dict[str, GroupStats],
    patterns: list[dict],
    dimensions: tuple[str, ...],
    total_jobs: int,
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        f"# Pattern Analysis — {today}",
        "",
        f"_Source: `jobs` table at {datetime.now(timezone.utc).isoformat()}._",
        f"_Total rows analyzed: {total_jobs}._",
        f"_Group-by: {', '.join(dimensions)}._",
        "",
    ]
    if patterns:
        lines.append("## Flagged patterns")
        lines.append("")
        lines.append("Groups whose response rate diverges from the global mean by")
        lines.append(f"at least {DEFAULT_EFFECT_SIZE_PP:.0f}pp, n ≥ {MIN_GROUP_SIZE}.")
        lines.append("")
        lines.append("| Group | n | applied | response_rate | Δ vs global |")
        lines.append("|---|---:|---:|---:|---:|")
        for p in patterns:
            lines.append(
                f"| `{p['group']}` | {p['n']} | {p['applied']} | "
                f"{p['response_rate']:.0%} | "
                f"{'+' if p['delta_pp_vs_global'] > 0 else ''}{p['delta_pp_vs_global']:.1f}pp |"
            )
        lines.append("")
    else:
        lines.append("_No groups flagged at the current effect-size threshold._")
        lines.append("")

    lines.append("## All groups")
    lines.append("")
    lines.append("| Group | n | applied | responded | interview | offer | response_rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for s in sorted(stats.values(), key=lambda s: -s.n):
        lines.append(
            f"| `{s.name}` | {s.n} | {s.applied} | {s.responded} | "
            f"{s.interviewed} | {s.offered} | {s.response_rate:.0%} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(report_md: str) -> Path:
    out_dir = _REPO_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir / f"patterns-{today}.md"
    out_path.write_text(report_md, encoding="utf-8")
    return out_path


def write_supabase_row(
    stats: dict[str, GroupStats],
    patterns: list[dict],
    dimensions: tuple[str, ...],
    total_jobs: int,
    summary_md: str,
) -> int:
    payload = {
        "groups": [s.as_dict() for s in stats.values()],
        "flagged_patterns": patterns,
    }
    row = {
        "num_jobs_analyzed": total_jobs,
        "dimensions": ",".join(dimensions),
        "payload": payload,
        "summary_md": summary_md,
    }
    res = client.table("pattern_analyses").insert(row).execute()
    inserted = res.data[0] if res.data else {}
    return int(inserted.get("id") or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Closed-loop pattern analysis (J-6)")
    parser.add_argument(
        "--dimensions",
        default=",".join(DEFAULT_DIMENSIONS),
        help="Comma-separated group-by dimensions. Supported: archetype, status, "
             "company_size, comp_band, ats, source, ats_kind. Default: %(default)s.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_EFFECT_SIZE_PP,
        help="Effect-size threshold (percentage points). Default: %(default)s.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the report to stdout instead of writing to disk + Supabase.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    dims = tuple(d.strip() for d in args.dimensions.split(",") if d.strip())

    jobs = fetch_jobs()
    logger.info("Loaded %d job rows for analysis (dims=%s)", len(jobs), dims)
    if not jobs:
        logger.warning("No jobs to analyze — exiting")
        return

    stats = aggregate(jobs, dims)
    patterns = find_patterns(stats, args.threshold)
    md = render_markdown(stats, patterns, dims, len(jobs))

    if args.no_write:
        print(md)
        return

    out_path = write_report(md)
    logger.info("Wrote markdown report -> %s", out_path)
    row_id = write_supabase_row(stats, patterns, dims, len(jobs), md)
    logger.info("Wrote Supabase pattern_analyses row id=%s", row_id)


if __name__ == "__main__":
    main()
