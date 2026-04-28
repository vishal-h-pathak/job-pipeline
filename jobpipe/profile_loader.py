"""jobpipe.profile_loader — single import surface for the user-layer profile.

Centralizes reads of files under the top-level `profile/` directory so callers
across `jobpipe.hunt`, `jobpipe.tailor`, and `jobpipe.submit` stop reaching
across sibling-repo paths via relative parent-directory hops.

Resolution order for the profile directory:
  1. `JOBPIPE_PROFILE_DIR` environment variable, if set and non-empty.
  2. Walk up from this file until `pyproject.toml` is found; use `<root>/profile/`.

A missing profile dir or missing individual file is not fatal: dict loaders
return `{}` and string loaders return `""`. Callers that need a stricter
contract should validate the result.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


def _walk_up_for_pyproject(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def profile_dir() -> Path:
    """Resolve the user-layer profile directory.

    Honors `JOBPIPE_PROFILE_DIR`, otherwise walks up from this module until
    `pyproject.toml` is found and returns `<repo_root>/profile`.
    """
    env_override = os.environ.get("JOBPIPE_PROFILE_DIR", "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()

    repo_root = _walk_up_for_pyproject(Path(__file__))
    if repo_root is None:
        raise RuntimeError(
            "profile_loader: could not locate pyproject.toml walking up from "
            f"{Path(__file__).resolve()}; set JOBPIPE_PROFILE_DIR to override."
        )
    return repo_root / "profile"


def _read_text(name: str) -> str:
    path = profile_dir() / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _read_yaml(name: str) -> dict:
    text = _read_text(name)
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def _clear_cache_for_tests() -> None:
    """Reset the cached profile_dir() resolution. Test helper only."""
    profile_dir.cache_clear()


# ── Public loaders ──────────────────────────────────────────────────────────


def load_profile() -> dict:
    """Return the full parsed `profile.yml` as a dict."""
    return _read_yaml("profile.yml")


def load_profile_text() -> str:
    """Return the raw text of `profile.yml` (empty string if missing).

    Useful when callers want to splice the file into an LLM prompt
    verbatim — comments and key ordering survive — rather than re-emit a
    parsed dict via ``yaml.safe_dump``. Used by
    ``jobpipe.hunt.prompts.build_profile_prompt_string``.
    """
    return _read_text("profile.yml")


def load_archetypes() -> dict:
    """Return the `archetypes:` block from `profile.yml` (empty dict if missing)."""
    archetypes = load_profile().get("archetypes")
    return archetypes if isinstance(archetypes, dict) else {}


def load_application_defaults() -> dict:
    """Return the `application_defaults:` block from `profile.yml`.

    Single source of truth for canonical form-fill answers (work auth, start
    date, relocation, in-person, AI-policy ack, previous-interview map).
    """
    defaults = load_profile().get("application_defaults")
    return defaults if isinstance(defaults, dict) else {}


def load_article_digest() -> str:
    """Return `profile/article-digest.md` contents (empty string if missing)."""
    return _read_text("article-digest.md")


def load_learned_insights() -> str:
    """Return `profile/learned-insights.md` contents (empty string if missing)."""
    return _read_text("learned-insights.md")


def load_voice_profile() -> dict:
    """Parse `profile/voice-profile.md` into a section-keyed dict.

    Splits on top-level `## ` headings; keys are kebab-cased section titles,
    values are the raw section bodies. The full unparsed text is also
    returned under the `raw` key so callers can pass the whole file to an
    LLM unmodified when needed.
    """
    text = _read_text("voice-profile.md")
    out: dict[str, object] = {"raw": text}
    if not text.strip():
        return out

    sections: dict[str, str] = {}
    current_key: Optional[str] = None
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            title = m.group(1).strip().lower()
            current_key = re.sub(r"[^a-z0-9]+", "-", title).strip("-")
            buf = []
        elif current_key is not None:
            buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()

    out["sections"] = sections
    return out
