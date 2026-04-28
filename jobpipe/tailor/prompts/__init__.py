"""prompts/ — versioned markdown prompts for job-applicant.

Each `.md` file in this folder is a system or user prompt for a specific
LLM call site. `_shared.md` holds global rules (ethics, anti-slop,
specificity, voice) that are prepended once to every prompt loaded via
`load_prompt`.

Prompts are templated with Python's `str.format` — placeholders look like
`{name}` and JSON braces in the body must be doubled (`{{ }}`).

Multiple prompts can be composed in one call:
    load_prompt("agent_common", "agent_prepare", job_title=..., company=...)
joins them in order with `---` separators, with `_shared.md` prepended once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_PROMPTS_DIR = Path(__file__).parent
_REPO_ROOT = _PROMPTS_DIR.parent
_SHARED_CACHE: Optional[str] = None
_PROFILE_CACHE: Optional[str] = None

# User-layer files in the order they should appear when concatenated for
# an LLM. profile.yml first (structured ground truth), then disqualifiers,
# then narrative artifacts (CV, article digest).
_USER_LAYER_FILES = (
    "profile.yml",
    "disqualifiers.yml",
    "cv.md",
    "article-digest.md",
)


def _resolve_profile_dir() -> Optional[Path]:
    """Find the user-layer profile/ directory.

    Resolution order:
      1. `<this repo>/profile/`
      2. `<sibling job-hunter>/profile/` (canonical home of the user layer)
      3. None — caller falls back to legacy `CLAUDE.md`.
    """
    local = _REPO_ROOT / "profile"
    if local.exists():
        return local
    sibling = _REPO_ROOT.parent / "job-hunter" / "profile"
    if sibling.exists():
        return sibling
    return None


def _shared() -> str:
    global _SHARED_CACHE
    if _SHARED_CACHE is None:
        _SHARED_CACHE = (_PROMPTS_DIR / "_shared.md").read_text(encoding="utf-8")
    return _SHARED_CACHE


def load_profile() -> str:
    """Load the merged user-layer profile.

    Concatenates `profile/profile.yml`, `profile/disqualifiers.yml`,
    `profile/cv.md`, and `profile/article-digest.md` (whichever exist)
    into a single string suitable for injecting into prompts. Falls back
    to legacy `CLAUDE.md` if no `profile/` directory is found in this
    repo or the sibling `job-hunter/`.
    """
    global _PROFILE_CACHE
    if _PROFILE_CACHE is not None:
        return _PROFILE_CACHE

    profile_dir = _resolve_profile_dir()
    if profile_dir is not None:
        parts: list[str] = []
        for name in _USER_LAYER_FILES:
            f = profile_dir / name
            if f.exists():
                parts.append(
                    f"========== {name} ==========\n"
                    + f.read_text(encoding="utf-8").strip()
                )
        _PROFILE_CACHE = "\n\n".join(parts) if parts else ""
        if _PROFILE_CACHE:
            return _PROFILE_CACHE

    # Last-resort fallback: legacy CLAUDE.md in this repo or sibling.
    for legacy in (_REPO_ROOT / "CLAUDE.md", _REPO_ROOT.parent / "job-hunter" / "CLAUDE.md"):
        if legacy.exists():
            _PROFILE_CACHE = legacy.read_text(encoding="utf-8")
            return _PROFILE_CACHE

    _PROFILE_CACHE = ""
    return _PROFILE_CACHE


def load_prompt(*names: str, **vars: object) -> str:
    """Load one or more prompts/{name}.md, format placeholders, prepend _shared.md.

    Args:
        *names: Prompt file stems (e.g. `"tailor_resume"`).
        **vars: Substitution variables. JSON braces in templates must be
            doubled to survive `.format()`.

    Returns:
        `_shared.md` + each prompt body, joined with `---` separators.
    """
    parts = [_shared()]
    for n in names:
        body = (_PROMPTS_DIR / f"{n}.md").read_text(encoding="utf-8")
        if vars:
            body = body.format(**vars)
        parts.append(body)
    return "\n\n---\n\n".join(parts)
