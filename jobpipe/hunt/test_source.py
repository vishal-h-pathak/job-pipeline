"""
test_source.py — smoke-test a single hunter source without touching Claude
scoring or Supabase.

Prints a summary and a sample of yielded rows for one source. Exits 0 on
success; non-zero if the source raises. Useful for verifying credential
setup or new-source parsers before paying for a full hunter run.

Usage:

    python3 test_source.py jsearch
    python3 test_source.py 80kh
    python3 test_source.py greenhouse
    python3 test_source.py jsearch --mode us_wide
    python3 test_source.py jsearch --limit 3
    python3 test_source.py jsearch --full   # print full row body, not preview

Source names:
    indeed, remoteok, serpapi, linkedin, greenhouse, ashby, hn_whoshiring,
    80kh (alias for eighty_thousand_hours), jsearch, wellfound
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

import config  # noqa: E402  (must come after load_dotenv)

# Source module aliases. Keep keys lowercase / canonical.
_ALIASES = {
    "indeed":         "sources.indeed",
    "remoteok":       "sources.remoteok",
    "serpapi":        "sources.serpapi",
    "linkedin":       "sources.linkedin",
    "greenhouse":     "sources.greenhouse",
    "ashby":          "sources.ashby",
    "hn":             "sources.hn_whoshiring",
    "hn_whoshiring":  "sources.hn_whoshiring",
    "80kh":           "sources.eighty_thousand_hours",
    "eighty_thousand_hours": "sources.eighty_thousand_hours",
    "80000hours":     "sources.eighty_thousand_hours",
    "jsearch":        "sources.jsearch",
    "wellfound":      "sources.wellfound",
}


def _preview(job: dict, full: bool) -> str:
    if full:
        return json.dumps(job, indent=2, default=str)
    desc = (job.get("description") or "")[:140]
    if len(job.get("description") or "") > 140:
        desc += "…"
    return (
        f"  [{job.get('source','?')}] {job.get('title','?')} — "
        f"{job.get('company','?')} · {job.get('location','?')}\n"
        f"      url: {job.get('url','')[:120]}\n"
        f"      desc: {desc}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Source name (e.g. jsearch, 80kh)")
    parser.add_argument(
        "--mode",
        choices=("local_remote", "us_wide"),
        default=None,
        help="Hunter mode override. Defaults to HUNTER_MODE env or local_remote.",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="Max rows to print (default 5). Source still iterates fully.",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="Print full job dicts instead of one-line previews.",
    )
    args = parser.parse_args()

    if args.mode:
        config.set_mode(args.mode)

    name = args.source.lower()
    mod_path = _ALIASES.get(name)
    if not mod_path:
        print(f"unknown source: {name!r}\nKnown: {sorted(set(_ALIASES))}",
              file=sys.stderr)
        return 2

    print(f"=== test_source: {name} (mode={config.get_mode()}) ===")

    try:
        module = __import__(mod_path, fromlist=["fetch"])
    except Exception as exc:
        print(f"failed to import {mod_path}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 3

    fetch = getattr(module, "fetch", None)
    if fetch is None:
        print(f"{mod_path} has no fetch() function", file=sys.stderr)
        return 4

    rows: list[dict] = []
    try:
        for job in fetch():
            rows.append(job)
    except Exception as exc:
        print(f"\nfetch() raised: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 5

    print(f"\ntotal rows yielded: {len(rows)}")
    if not rows:
        print("(no rows — check the source's logs above for diagnostics)")
        return 0

    # Tally by company so noisy / empty fields are visible at a glance.
    by_company: dict[str, int] = {}
    for r in rows:
        c = r.get("company") or "?"
        by_company[c] = by_company.get(c, 0) + 1
    top_companies = sorted(by_company.items(), key=lambda x: x[1], reverse=True)[:8]
    print(f"top companies: {top_companies}")

    print(f"\n--- first {min(args.limit, len(rows))} rows ---")
    for r in rows[: args.limit]:
        print(_preview(r, args.full))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
