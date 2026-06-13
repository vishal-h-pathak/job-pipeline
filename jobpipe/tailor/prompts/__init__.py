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

from jobpipe import profile_loader

_PROMPTS_DIR = Path(__file__).parent
_SHARED_CACHE: Optional[str] = None
_PROFILE_CACHE: Optional[str] = None

# Banner glued onto thesis.md wherever it is spliced into an LLM prompt.
# Mirrors jobpipe.hunt.prompts.build_profile_prompt_string — thesis.md is
# the canonical judgment document and must go FIRST, with the
# wins-on-conflict statement, in every profile context the tailor builds.
_THESIS_BANNER = (
    "========== thesis.md (CANONICAL — read first) ==========\n"
    "The hunting thesis below is the most recent, authoritative "
    "statement of what Vishal is looking for. Where it conflicts "
    "with any other profile document in this prompt, thesis.md "
    "wins.\n\n"
)


def thesis_section() -> str:
    """Return the bannered canonical thesis block, or "" if thesis.md is
    missing. Callers that build their own context (the archetype
    router) splice this in as their FIRST profile document."""
    thesis = profile_loader.load_thesis().strip()
    if not thesis:
        return ""
    return _THESIS_BANNER + thesis


# Degree-gate framing (Session I, thesis.md degree-gate rule). Injected
# into the cover-letter and form-answers prompts ONLY when the scorer
# flagged jobs.degree_gated — a JD that hard-requires an MS/PhD with no
# equivalent-experience escape hatch. The move is preemption, not
# evasion: lead with the equivalence case, never imply a degree he lacks.
_DEGREE_GATE_BLOCK = """\
DEGREE GATE — this JD hard-requires an advanced degree (MS/PhD) with no
"or equivalent experience" escape hatch. Binding rules for this output:

- LEAD with the equivalence case: Vishal has a BS in Electrical
  Engineering plus nine years of hands-on neuromorphic/embedded work —
  Rain Neuromorphics employee #5 at 19 building memristive neuron PCBs,
  four years deploying SNNs on Intel Loihi-class hardware and writing
  VHDL neuron models at GTRI. Present that record explicitly as the
  equivalent of the listed degree requirement, in the opening (cover
  letter first paragraph / why_this_role first sentences) — preempt the
  gate before the reader applies it, don't bury the case at the end.
- HONESTY UNCHANGED: never claim or imply a degree he doesn't have. No
  "MS-level", no vague "graduate work", no degree-adjacent hedging. The
  move is preemption, not evasion: name the BS, then make the case that
  the work itself is the qualification."""


def degree_gate_block(job: dict) -> str:
    """Return the degree-gate framing block when the job row carries
    ``degree_gated=true``, else the empty string. Callers splice the
    result into their prompt unconditionally — ungated jobs get no gate
    framing at all."""
    return _DEGREE_GATE_BLOCK if job.get("degree_gated") else ""

# User-layer files in the order they should appear when concatenated for
# an LLM. profile.yml first (structured ground truth), then disqualifiers,
# then narrative artifacts (CV, article digest).
_USER_LAYER_FILES = (
    "profile.yml",
    "disqualifiers.yml",
    "cv.md",
    "article-digest.md",
    # J-11 — Match Agent appends generalizable preferences here. Loaded
    # last so insights override earlier statements when they conflict.
    "learned-insights.md",
)


def _walk_up_for_pyproject(start: Path) -> Optional[Path]:
    """Walk up from ``start`` until a directory containing pyproject.toml is found."""
    cur = start.resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _resolve_profile_search_dirs() -> tuple[Path, ...]:
    """Return the directories that may contain user-layer profile files.

    PR-9: the unified jobpipe repo splits the user layer across two
    locations: the structured + narrative files (``profile.yml``,
    ``article-digest.md``, ``learned-insights.md``,
    ``voice-profile.md``) live at the repo-root ``profile/`` directory,
    while the hunt-specific files (``cv.md``, ``disqualifiers.yml``,
    ``portals.yml``) live at ``jobpipe/hunt/profile/`` because they're
    only consumed by the hunter's source-side filtering. ``load_profile``
    scans both and concatenates whichever files it finds.
    """
    repo_root = _walk_up_for_pyproject(_PROMPTS_DIR)
    if repo_root is None:
        return ()
    dirs: list[Path] = []
    top = repo_root / "profile"
    if top.exists():
        dirs.append(top)
    hunt = repo_root / "jobpipe" / "hunt" / "profile"
    if hunt.exists():
        dirs.append(hunt)
    return tuple(dirs)


def _shared() -> str:
    global _SHARED_CACHE
    if _SHARED_CACHE is None:
        _SHARED_CACHE = (_PROMPTS_DIR / "_shared.md").read_text(encoding="utf-8")
    return _SHARED_CACHE


def load_profile() -> str:
    """Load the merged user-layer profile.

    Concatenates ``profile.yml`` + ``disqualifiers.yml`` + ``cv.md`` +
    ``article-digest.md`` + ``learned-insights.md`` (whichever exist)
    into a single string suitable for injecting into prompts. PR-9: the
    user-layer files now live at unified repo-root locations
    (``profile/`` + ``jobpipe/hunt/profile/``) — see
    :func:`_resolve_profile_search_dirs`. Falls back to the consolidated
    repo-root ``CLAUDE.md`` if no profile files are found.
    """
    global _PROFILE_CACHE
    if _PROFILE_CACHE is not None:
        return _PROFILE_CACHE

    search_dirs = _resolve_profile_search_dirs()
    if search_dirs:
        parts: list[str] = []
        # thesis.md is canonical and goes FIRST — same contract as the
        # hunt scorer's build_profile_prompt_string.
        thesis = thesis_section()
        if thesis:
            parts.append(thesis)
        for name in _USER_LAYER_FILES:
            for d in search_dirs:
                f = d / name
                if f.exists():
                    parts.append(
                        f"========== {name} ==========\n"
                        + f.read_text(encoding="utf-8").strip()
                    )
                    break
        _PROFILE_CACHE = "\n\n".join(parts) if parts else ""
        if _PROFILE_CACHE:
            return _PROFILE_CACHE

    # Last-resort fallback: the repo-root narrative CLAUDE.md (PR-9
    # consolidated the per-subpackage CLAUDE.md files into one top-level
    # file).
    repo_root = _walk_up_for_pyproject(_PROMPTS_DIR)
    if repo_root is not None:
        legacy = repo_root / "CLAUDE.md"
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
