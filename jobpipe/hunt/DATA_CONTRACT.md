# Data Contract — Two-Layer File Segregation

Borrowed from `santifer/career-ops`. The repo is split into two layers
that have different ownership and replacement semantics.

## User Layer (never replaced by code updates)

Hand-edited by Vishal. Future code refactors must not overwrite, rename,
or repurpose these files without explicit consent. They are the single
source of truth for "who the candidate is" — every prompt and form-fill
inherits from them.

| Path | What lives here |
|---|---|
| `profile/profile.yml` | Identity, location, comp, tiers, skill list, application form defaults |
| `profile/disqualifiers.yml` | Hard disqualifiers + soft concerns the scorer reads |
| `profile/cv.md` | Master CV in markdown — single source of truth for resume content |
| `profile/article-digest.md` | Curated proof points + metrics (cited in cover letters and tailored bullets) |
| `CLAUDE.md` | Compatibility view; mirrors `profile/` in narrative form. Will be retired once all callers read from `profile/` directly. |
| `prompts/_shared.md` | Global rules (anti-slop, ethics, specificity). Shared across every prompt. |

## System Layer (replaceable)

Code, prompts, and configuration that downstream refactors may freely
edit. These files implement *how* the system uses the user layer; the
user layer remains stable while the system layer evolves.

| Path | Role |
|---|---|
| `*.py` | Scorer, sources, orchestration, db helpers, validators, enrichers |
| `prompts/scorer.md`, `prompts/legitimacy.md`, ... (added in J-2 onward) | Task bodies that consume the user layer at call time |
| `sources/*.py` | ATS API scanners (Greenhouse, Lever, Ashby, Workday) |
| `scripts/*.py` | One-off scripts (liveness rechecker, drift detector) |
| `seen_jobs.json` | Per-process dedup cache |
| `requirements.txt`, `run_agent.sh` | Build / runtime scaffolding |

## Reading the user layer

`prompts/__init__.py::load_profile()` aggregates the user-layer files into
a single string suitable for injecting into prompts. Callers should
always go through that helper rather than reading `CLAUDE.md` directly,
so the source-of-truth migration is a single-file change.
