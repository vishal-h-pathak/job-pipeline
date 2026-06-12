"""Session G — portals.yml schema validity + title-filter sanity.

The portal map is hand-edited YAML; a typo'd row fails silently at run
time (sources drop bad slugs with a warning). These tests make the
schema contract explicit and pin the thesis-relevant titles the reject
list must never block.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from jobpipe.hunt.sources._portals import passes_title_filter

REPO_ROOT = Path(__file__).resolve().parent.parent
PORTALS = REPO_ROOT / "jobpipe" / "hunt" / "profile" / "portals.yml"


def _load() -> dict:
    return yaml.safe_load(PORTALS.read_text(encoding="utf-8")) or {}


def test_portals_yaml_parses_with_expected_sections() -> None:
    data = _load()
    for key in ("greenhouse", "lever", "ashby", "workday", "title_filter"):
        assert key in data, f"portals.yml missing section: {key}"


def test_company_rows_have_slug_and_name() -> None:
    data = _load()
    for platform in ("greenhouse", "lever", "ashby"):
        rows = (data.get(platform) or {}).get("companies") or []
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict), f"{platform}: non-dict row {row!r}"
            assert isinstance(row.get("slug"), str) and row["slug"].strip(), (
                f"{platform}: row missing slug: {row!r}"
            )
            assert isinstance(row.get("name"), str) and row["name"].strip(), (
                f"{platform}: row missing name: {row!r}"
            )


def test_no_duplicate_slugs_per_platform() -> None:
    data = _load()
    for platform in ("greenhouse", "lever", "ashby"):
        rows = (data.get(platform) or {}).get("companies") or []
        slugs = [r["slug"] for r in rows]
        assert len(slugs) == len(set(slugs)), f"{platform}: duplicate slugs"


def test_title_filter_lists_are_clean_strings() -> None:
    tf = _load().get("title_filter") or {}
    for key in ("reject_substrings", "prefer_substrings", "seniority_substrings"):
        values = tf.get(key) or []
        assert isinstance(values, list) and values, f"{key} empty or missing"
        for v in values:
            assert isinstance(v, str) and v.strip(), f"{key}: bad entry {v!r}"


# ── Task 5: the reject list must not block thesis-relevant titles ───────

THESIS_TITLES_THAT_MUST_PASS = [
    "Forward Deployed Engineer",
    "Agent Engineer",
    "Applied AI Engineer",
    "Member of Technical Staff",
    "Research Engineer",
    "Solutions Engineer",
    # Belt-and-braces variants seen in the wild
    "Forward-Deployed AI Engineer",
    "Applied AI, Member of Technical Staff",
    "Research Engineer, Model Evaluations",
]


def test_thesis_relevant_titles_pass_reject_filter() -> None:
    blocked = [t for t in THESIS_TITLES_THAT_MUST_PASS if not passes_title_filter(t)]
    assert not blocked, f"reject_substrings blocks thesis titles: {blocked}"


def test_reject_filter_still_rejects_obvious_noise() -> None:
    for title in ("VP of Engineering", "Technical Recruiter", "Office Manager",
                  "Software Engineering Internship"):
        assert not passes_title_filter(title), f"should reject: {title}"
