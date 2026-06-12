#!/usr/bin/env python3
"""scripts/check_profile_sync.py — flag drift between profile.yml and CLAUDE.md.

``profile/profile.yml`` is the user-layer ground truth; the repo-root
``CLAUDE.md`` carries a hand-mirrored prose form of the same facts for
LLM prompt context. The mirror is maintained by hand, so facts drift
(Session C's motivating example: the book-club URL read ``papercuts.org``
in the narrative long after the real site moved to ``papercuts.cc``).

This script diffs the small set of facts most likely to drift — URLs,
the comp band, and the base location — and exits non-zero when the
narrative disagrees with the YAML. CI runs it as a soft warning step
(``continue-on-error``), so drift annotates the build without failing it.

Checks:
  1. Location — ``identity.location_base`` must appear verbatim in CLAUDE.md.
  2. Comp band — every dollar figure in ``location_and_compensation``
     (current + target endpoints) must appear in CLAUDE.md in its
     thousands form (e.g. 110000 → "110k", allowing "$110k"/"~$110k").
  3. URLs — for every domain written in profile.yml, if CLAUDE.md
     mentions the same site (same first label, e.g. "papercuts"), the
     full domain must match exactly. This catches TLD drift
     (papercuts.org vs papercuts.cc) without requiring every YAML URL
     to appear in the narrative.

Usage:
    python scripts/check_profile_sync.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profile" / "profile.yml"
CLAUDE_MD_PATH = REPO_ROOT / "CLAUDE.md"

# Domain-shaped tokens: ≥2 dot-separated labels ending in an alpha TLD.
# Lowercase-only on purpose — prose like "RC circuits scaling to..." and
# class paths like ``jobpipe.tailor.paths`` contain dotted tokens, but
# real site mentions in these files are all-lowercase domains.
_DOMAIN_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+[a-z]{2,})\b")

# Dotted tokens that look like domains but aren't sites: python module
# paths and filenames that appear inside prose or YAML comments.
_NON_SITE_TLDS = {"py", "yml", "yaml", "md", "json", "sql", "txt", "pdf", "png", "html"}
_NON_SITE_PREFIXES = ("jobpipe.", "scripts.", "tests.")


def _domains(text: str) -> set[str]:
    found = set()
    for match in _DOMAIN_RE.findall(text):
        if match.rsplit(".", 1)[-1] in _NON_SITE_TLDS:
            continue
        if match.startswith(_NON_SITE_PREFIXES):
            continue
        found.add(match)
    return found


def _comp_figures(comp: dict) -> set[str]:
    """Dollar figures from the comp block, as thousands strings ('110')."""
    figures: set[str] = set()
    for key in ("current_comp_usd", "target_comp_usd"):
        raw = str(comp.get(key, ""))
        for number in re.findall(r"\d{4,}", raw):
            if int(number) % 1000 == 0:
                figures.add(str(int(number) // 1000))
    return figures


def main() -> int:
    profile = yaml.safe_load(PROFILE_PATH.read_text())
    narrative = CLAUDE_MD_PATH.read_text()

    problems: list[str] = []

    # 1. Location
    location = profile.get("identity", {}).get("location_base", "")
    if location and location not in narrative:
        problems.append(
            f"location: profile.yml says {location!r} but CLAUDE.md never states it"
        )

    # 2. Comp band (110000 → "110k", allow "$110k" / "~$110k", and range
    #    notation where the figure is the lower endpoint: "$120–140k")
    comp = profile.get("location_and_compensation", {})
    for figure in sorted(_comp_figures(comp)):
        if not re.search(rf"{figure}(?:\s*[–—-]\s*\d+)?\s*k\b", narrative, re.IGNORECASE):
            problems.append(
                f"comp: profile.yml includes ${figure}k but CLAUDE.md never mentions it"
            )

    # 3. URL drift: same site root mentioned, different full domain
    yaml_domains = _domains(PROFILE_PATH.read_text())
    narrative_domains = _domains(narrative)
    for domain in sorted(yaml_domains):
        root = domain.split(".", 1)[0]
        rivals = {
            d for d in narrative_domains
            if d.split(".", 1)[0] == root and d != domain
        }
        if rivals:
            problems.append(
                f"url: profile.yml says {domain!r} but CLAUDE.md says "
                f"{', '.join(repr(r) for r in sorted(rivals))}"
            )

    if problems:
        print(f"profile drift: {len(problems)} fact(s) out of sync "
              f"between profile/profile.yml and CLAUDE.md\n")
        for problem in problems:
            print(f"  - {problem}")
        print("\nprofile/profile.yml is canonical — update CLAUDE.md to match.")
        return 1

    print("profile sync OK: CLAUDE.md narrative matches profile/profile.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
