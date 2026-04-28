"""sources/_portals.py — Shared portal-config + title-filter helpers (J-1).

Loads `profile/portals.yml` (canonical user-layer ATS map) and exposes:

- `companies(platform)` — list of (slug, name) tuples for a given ATS
- `passes_title_filter(title)` — cheap pre-filter so we skip LLM scoring
  on obvious leadership / non-engineering postings
- `title_signals(title)` — debug helper returning matched
  prefer/seniority substrings (logged but doesn't gate filtering)

The fallback path keeps the hunter running if `portals.yml` is missing
— `companies()` returns an empty list, `passes_title_filter()` returns
True. This makes the move from hard-coded lists to YAML opt-in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("sources._portals")

_PORTALS_CACHE: Optional[dict] = None
_PORTALS_PATH = Path(__file__).parent.parent / "profile" / "portals.yml"


def _load_portals() -> dict:
    global _PORTALS_CACHE
    if _PORTALS_CACHE is not None:
        return _PORTALS_CACHE
    if not _PORTALS_PATH.exists():
        logger.warning("portals.yml not found at %s — sources will fall back to in-module defaults", _PORTALS_PATH)
        _PORTALS_CACHE = {}
        return _PORTALS_CACHE
    try:
        import yaml  # PyYAML — already a transitive dep via requirements
    except ImportError:
        logger.warning("PyYAML not installed; install pyyaml to read portals.yml")
        _PORTALS_CACHE = {}
        return _PORTALS_CACHE
    _PORTALS_CACHE = yaml.safe_load(_PORTALS_PATH.read_text(encoding="utf-8")) or {}
    return _PORTALS_CACHE


def companies(platform: str) -> list[tuple[str, str]]:
    """Return [(slug, name), ...] for a given ATS platform.

    Returns [] if the platform is missing from `portals.yml` so callers
    can keep their existing in-module hard-coded fallbacks during the
    cutover.
    """
    cfg = _load_portals().get(platform) or {}
    rows = cfg.get("companies") or []
    out: list[tuple[str, str]] = []
    for row in rows:
        slug = (row.get("slug") or "").strip()
        name = (row.get("name") or slug).strip()
        if slug:
            out.append((slug, name))
    return out


def workday_tenants() -> list[dict]:
    """Workday rows carry richer metadata than slug+name (tenant, site, dc).

    Returns the raw list of dicts from `portals.yml::workday.companies`.
    Each dict is expected to have at least: tenant, site, dc, name.
    """
    cfg = _load_portals().get("workday") or {}
    return list(cfg.get("companies") or [])


def _filter_cfg() -> dict:
    return _load_portals().get("title_filter") or {}


def passes_title_filter(title: str) -> bool:
    """Cheap title pre-filter applied before the LLM scorer touches a job.

    Reject if the title contains any `reject_substrings` term
    (case-insensitive substring match). Otherwise pass — the scorer can
    take it from there. Conservative on purpose: a slightly noisy match
    that survives is cheaper than missing a Tier 1 role.
    """
    if not title:
        return True
    cfg = _filter_cfg()
    rejects: Iterable[str] = cfg.get("reject_substrings") or []
    needle = title.lower()
    for r in rejects:
        if r and r.lower() in needle:
            return False
    return True


def title_signals(title: str) -> dict:
    """Debug helper: which prefer/seniority substrings matched a title.

    Used for logging only — does not affect filtering. Returns a dict
    {prefer: [...], seniority: [...]} with the matches found.
    """
    cfg = _filter_cfg()
    needle = (title or "").lower()
    prefer = [s for s in (cfg.get("prefer_substrings") or []) if s and s.lower() in needle]
    seniority = [s for s in (cfg.get("seniority_substrings") or []) if s and s.lower() in needle]
    return {"prefer": prefer, "seniority": seniority}
