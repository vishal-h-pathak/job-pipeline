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
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Allow `python -m scripts.analyze_patterns` from the repo root.
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from jobpipe.db import client  # noqa: E402

logger = logging.getLogger("analyze_patterns")

# 'applied' is the sole canonical post-submission status (migration 011
# guarantees canonical-only). Employer outcomes live on their own axis:
# jobs.response_status (migration 012, Session I) — none → rejected |
# screen | interview | offer — written by the portfolio's "log response"
# feature. The pre-012 RESPONDED/INTERVIEW/OFFER status sets referenced
# values that never existed in the canonical enum, so every rate they
# fed was structurally zero; the buckets below consume the real column.
APPLIED_STATUSES = {"applied"}
RESPONSE_STATUSES = {"rejected", "screen", "interview", "offer"}
INTERVIEW_RESPONSES = {"interview", "offer"}
OFFER_RESPONSES = {"offer"}

# Default group-by dimensions. Any of these can be overridden at the CLI.
DEFAULT_DIMENSIONS = ("archetype", "ats_kind")

# Callback feedback loop (P2, feat/callback-feedback-loop) — resume_variant
# (latex_resume.py style lane) and company_type (hunt scorer taxonomy) are
# plain jobs columns, so `_project_dimensions`'s generic `job.get(d)` branch
# already groups by them; no special-casing needed there. This tuple drives
# the always-on "which resume version wins where" report below, independent
# of whatever --dimensions the caller picked for the primary pattern scan.
VARIANT_TYPE_DIMENSIONS = ("company_type", "resume_variant")

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
        # Outcomes ride jobs.response_status (migration 012); rows
        # predating the column or untouched by "log response" read as
        # 'none' and count nowhere below.
        response = (job.get("response_status") or "none").lower()
        if response in RESPONSE_STATUSES:
            b["responded"] += 1
        if response in INTERVIEW_RESPONSES:
            b["interviewed"] += 1
        if response in OFFER_RESPONSES:
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


def which_wins_where(jobs: list[dict]) -> list[dict]:
    """"Which resume version wins where / which to retire" — P2 adoption
    of the doc's Resume Intelligence module, done with jobpipe's actual
    reply/interview data instead of vibes.

    Groups by (company_type, resume_variant), keeps only variant groups
    within a company_type that clear ``MIN_GROUP_SIZE`` applications,
    and for each company_type with >=2 comparable variants reports the
    best- and worst-performing variant by response rate. A
    ``retire_candidate`` is named only when the best/worst gap clears
    ``DEFAULT_EFFECT_SIZE_PP`` — otherwise the difference is noise, not
    a real signal to act on.

    A company_type with 0 or 1 qualifying variants is omitted — there's
    nothing to compare a single variant (or none) against.
    """
    stats = aggregate(jobs, VARIANT_TYPE_DIMENSIONS)
    by_type: dict[str, list[GroupStats]] = defaultdict(list)
    for key, s in stats.items():
        if s.n < MIN_GROUP_SIZE or s.applied < 1:
            continue
        company_type, _, variant = key.partition(" · ")
        by_type[company_type].append(
            GroupStats(name=variant, n=s.n, applied=s.applied,
                       responded=s.responded, interviewed=s.interviewed,
                       offered=s.offered)
        )

    report: list[dict] = []
    for company_type in sorted(by_type):
        variants = by_type[company_type]
        if len(variants) < 2:
            continue
        ranked = sorted(variants, key=lambda v: -v.response_rate)
        best, worst = ranked[0], ranked[-1]
        delta_pp = (best.response_rate - worst.response_rate) * 100
        report.append({
            "company_type": company_type,
            "variants_compared": [v.as_dict() for v in ranked],
            "best_variant": best.name,
            "best_response_rate": round(best.response_rate, 4),
            "worst_variant": worst.name,
            "worst_response_rate": round(worst.response_rate, 4),
            "delta_pp": round(delta_pp, 1),
            "retire_candidate": (
                worst.name if delta_pp >= DEFAULT_EFFECT_SIZE_PP else None
            ),
        })
    return report


def render_markdown(
    stats: dict[str, GroupStats],
    patterns: list[dict],
    dimensions: tuple[str, ...],
    total_jobs: int,
    wins_where: list[dict] | None = None,
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

    if wins_where:
        lines.append("## Which resume version wins where / which to retire")
        lines.append("")
        lines.append(
            f"Resume variant compared within each company_type, n ≥ {MIN_GROUP_SIZE} "
            "applications per variant. `retire_candidate` is named only when the "
            f"best/worst response-rate gap clears {DEFAULT_EFFECT_SIZE_PP:.0f}pp."
        )
        lines.append("")
        lines.append("| Company type | Best variant | Best rate | Worst variant | Worst rate | Retire? |")
        lines.append("|---|---|---:|---|---:|---|")
        for w in wins_where:
            lines.append(
                f"| `{w['company_type']}` | `{w['best_variant']}` | "
                f"{w['best_response_rate']:.0%} | `{w['worst_variant']}` | "
                f"{w['worst_response_rate']:.0%} | "
                f"{'`' + w['retire_candidate'] + '`' if w['retire_candidate'] else '—'} |"
            )
        lines.append("")
    elif wins_where is not None:
        lines.append(
            "_Not enough data yet to compare resume variants within a "
            "company_type (need >=2 variants at n >= "
            f"{MIN_GROUP_SIZE} applications each)._"
        )
        lines.append("")

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
    wins_where: list[dict] | None = None,
) -> int:
    payload = {
        "groups": [s.as_dict() for s in stats.values()],
        "flagged_patterns": patterns,
        "which_wins_where": wins_where or [],
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
    # Always-on P2 callback feedback loop: resume_variant × company_type
    # "which version wins where" runs regardless of the caller's chosen
    # --dimensions, since it answers a different question (materials
    # comparison, not archetype/ATS pattern-hunting).
    wins_where = which_wins_where(jobs)
    md = render_markdown(stats, patterns, dims, len(jobs), wins_where=wins_where)

    if args.no_write:
        print(md)
        return

    out_path = write_report(md)
    logger.info("Wrote markdown report -> %s", out_path)
    row_id = write_supabase_row(stats, patterns, dims, len(jobs), md, wins_where=wins_where)
    logger.info("Wrote Supabase pattern_analyses row id=%s", row_id)


if __name__ == "__main__":
    main()
