# Data Contract — Two-Layer File Segregation

Borrowed from `santifer/career-ops`. The repo is split into two layers
that have different ownership and replacement semantics.

## User Layer (never replaced by code updates)

Hand-edited by Vishal. Lives canonically in the **sibling `job-hunter`
repo** under `job-hunter/profile/`. The applicant service reads from
there at runtime so the two services stay in sync without copy/paste.

| Path | What lives here |
|---|---|
| `../job-hunter/profile/profile.yml` | Identity, location, comp, tiers, skill list, application form defaults |
| `../job-hunter/profile/disqualifiers.yml` | Hard disqualifiers + soft concerns |
| `../job-hunter/profile/cv.md` | Master CV in markdown — single source of truth for resume content |
| `../job-hunter/profile/article-digest.md` | Proof points + metrics |
| `CLAUDE.md` | Compatibility view; will be retired once all callers read `profile/`. |
| `templates/VOICE_PROFILE.md` | Voice profile (tone for cover-letter prose). Kept in this repo because it's tailoring-specific. |
| `prompts/_shared.md` | Global rules (anti-slop, ethics, specificity, voice). Shared across every prompt. |

If `../job-hunter/profile/` is missing, `prompts.load_profile()` falls
back to local `CLAUDE.md` so the service still runs in isolation.

## System Layer (replaceable)

| Path | Role |
|---|---|
| `*.py` | Tailoring, applicant submission agents, db helpers |
| `prompts/tailor_*.md`, `agent_*.md` | Task bodies that consume the user layer + voice profile at call time |
| `applicant/*.py` | ATS-specific submission handlers (Ashby, universal) |
| `scripts/*.py` | One-off scripts (CV-sync drift detector, pattern analysis) |
| `tailor/latex_resume.py::BASE_RESUME` | Structured resume data; must mirror `profile/cv.md` (J-9 detects drift) |
| `requirements.txt`, `migration.sql` | Build / DB scaffolding |

## Reading the user layer

`prompts/__init__.py::load_profile()` aggregates the user-layer files
into a single string suitable for injecting into prompts. Callers should
always go through that helper.
