# Data Contract — Two-Layer File Segregation

Borrowed from `santifer/career-ops`. The repo is split into two layers
that have different ownership and replacement semantics.

## User Layer (never replaced by code updates)

Hand-edited by Vishal. PR-9 consolidated the user layer into two
unified locations under the `jobpipe` repo:

- Repo-root `profile/` — the structured + narrative files shared
  across hunt / tailor / submit.
- `jobpipe/hunt/profile/` — the hunt-only files used during source-side
  filtering and scoring.

Both locations are scanned by `prompts.load_profile()` (see
`jobpipe/tailor/prompts/__init__.py::_resolve_profile_search_dirs`).

| Path | What lives here |
|---|---|
| `profile/profile.yml` | Identity, location, comp, tiers, skill list, application form defaults |
| `profile/article-digest.md` | Proof points + metrics |
| `profile/learned-insights.md` | Match-Agent appended generalizable preferences (J-11) |
| `profile/voice-profile.md` | Voice profile (tone for cover-letter prose) |
| `jobpipe/hunt/profile/cv.md` | Master CV in markdown — single source of truth for resume content |
| `jobpipe/hunt/profile/disqualifiers.yml` | Hard disqualifiers + soft concerns |
| `jobpipe/hunt/profile/portals.yml` | Hunt source company list + per-portal title-filter |
| `<repo_root>/CLAUDE.md` | Narrative aggregator + last-resort fallback for `prompts.load_profile()` (PR-9 consolidated the three per-subpackage CLAUDE.md files into this single top-level file). |
| `prompts/_shared.md` | Global rules (anti-slop, ethics, specificity, voice). Shared across every prompt. |

If neither user-layer directory has any matching files,
`prompts.load_profile()` falls back to the repo-root `CLAUDE.md`.

## System Layer (replaceable)

| Path | Role |
|---|---|
| `*.py` | Tailoring, applicant submission agents, db helpers |
| `prompts/tailor_*.md`, `agent_*.md` | Task bodies that consume the user layer + voice profile at call time |
| `applicant/*.py` | ATS-specific submission handlers (Ashby, universal) |
| `scripts/*.py` | One-off scripts (CV-sync drift detector, pattern analysis) |
| `tailor/latex_resume.py::BASE_RESUME` | Structured resume data; must mirror `jobpipe/hunt/profile/cv.md` (J-9 detects drift) |
| `pyproject.toml`, `scripts/*.sql` | Build / DB scaffolding (PR-9 retired the per-subpackage `requirements.txt`; deps live in `pyproject.toml::[project].dependencies`). |

## Reading the user layer

`prompts/__init__.py::load_profile()` aggregates the user-layer files
into a single string suitable for injecting into prompts. Callers should
always go through that helper.
