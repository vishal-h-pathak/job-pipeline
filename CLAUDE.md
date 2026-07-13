> Cross-project context: ~/dev/jarvis/memory/INDEX.md — read it before asking the user to re-explain. This repo's capsule: ~/dev/jarvis/memory/projects/job-pipeline.md

# Vishal Pathak — Agent Profile

> Structured truth lives in `profile/profile.yml`; voice rules in `profile/voice-profile.md`.
> This file is the human-readable narrative aggregator the tailor reads as
> LLM prompt context (`jobpipe.tailor.paths.CANDIDATE_PROFILE_PATH`).
> Edit the structured files in `profile/` first; mirror to this file when
> the tailor needs the prose form.

PR-9 consolidated the three previous repo-level `CLAUDE.md` files (one
each under `jobpipe/{hunt,tailor,submit}/`) into this single file. The
identity prose lives here once; subpackage-specific design rules live in
each subpackage's `README.md`.

## Identity

EE background; has been working in AI since 2017, and that throughline is
the story. It began in neuromorphic hardware and brain-inspired computing
— the Hodgkin-Huxley model in college (ion channels as RC circuits
scaling to cognition), Rain Neuromorphics at 19 as employee #5
hand-building memristive LIF neuron PCBs, then four years at GTRI (SNN
deployment on Intel Kapoho Bay, VHDL neuron modeling, and a steadily
widening applied skillset: computer vision, embedded ML, real
deployment). That arc has brought him to the present: building tools
across many domains with frontier agentic workflows, and learning as much
as he can about these tools to stay ahead as AI advancement accelerates.
Neuromorphics/SNNs are now the depth that explains how he thinks (systems,
emergence), not the pitch — the present-tense identity is agentic builder.

## What he's looking for

> One-liner (his words): "I want a new job that lets me continue doing
> what I've been doing: using frontier agentic workflows to build tools
> for whomever needs them."

**Tier 1:** Agentic / applied AI engineering — building agentic tools and
products with frontier models, applied agent engineering,
forward-deployed/applied AI. The clear current target as of mid-2026.
**Tier 2:** Sales / solutions / forward-deployed engineering in AI/LLM.
Strong communicator, rare technical depth, no formal sales experience but
has pitched to DoD sponsors. Domain must be genuinely interesting.
**Tier 3:** ML/CV engineering, or computational neuroscience /
neuromorphic / connectomics / embodied sim / BCI (eon.systems was the
reference role). Was tier 1 earlier in the 2026 search; still of genuine
interest but no longer the primary lean. Heavily dependent on the company.

> **Positioning note:** earlier in the 2026 search neuro/neuromorphic was
> tier 1. As of mid-2026 that flipped — agentic-workflow / frontier-model
> work is primary. Treat neuro as origin and depth, not the headline.

**Disqualifiers:**
- DoD/defense contracts, government, roles with no clear mission.
- Academic positions (postdoc, professor, PhD programs) — no PhD.

## Location & compensation

- Atlanta, GA. Open to fully remote.
- Open to relocation only if mission + comp are both exceptional.
  eon.systems is the bar.
- Current: ~$110k. Target: $120–140k. Will consider same comp for the
  right role.

## How he works

Good communicator, creative problem-solver, works best with clear
direction and a compelling reason to solve the problem. Self-aware about
needing external structure to stay focused. Strong once pointed at
something.

## Key technical skills

Frontier agentic workflows — end-to-end tool building (this job pipeline,
Meridian trading telemetry, Cellular Gaits, the portfolio site,
Papercuts); multi-machine agent orchestration; LLM app plumbing
(Anthropic SDK, Supabase, Next.js/React, Playwright). FlyGym, MuJoCo,
Brian2, Gymnasium API, VHDL SNN implementation, Intel Kapoho Bay (Loihi
1/2), memristive hardware, DNN→SNN conversion, PyTorch, TensorFlow, HPC
training, RT-DETRv2, embedded ML (Jetson Orin), PCB design
(EagleCAD/Altium), PyQt6 desktop GUI development, serial protocol
integration (RS-232, RS-485), ruggedized sensor + cable deployment, AFSIM
surrogate modeling, C++, Python.

## Portfolio goal

`vishal.pa.thak.io` should showcase one thing: him — his interests, the
jobs and path that got him here (AI since 2017: neuromorphics → GTRI →
agentic building), the wide skillset from the GTRI years, and the new
agentic work he's doing now. The organizing throughline is "working in AI
since 2017 brought me here," present-tense identity an agentic builder of
tools across domains; neuromorphics is the deep origin discovered one
layer in, not the front door. The diverse project set is evidence for
"tools for whomever needs them," not sprawl. Prioritize personality and
genuine content over polish; it should not read as a generated candidate
page.

## Personal

From Cape Canaveral, FL. Moved to Atlanta April 2022. Runs a book club
(papercuts.cc). Into cooking, audiobooks, agentic AI projects.

## Application form defaults

Canonical answers the submitter's three-tier classifier reads (see
`jobpipe/submit/adapters/_common.py::applicant_fields`). When the tailor
populates `applicant_profile` on each `jobs` row, these values flow
through verbatim.

- `work_authorization`: `us_citizen`
- `visa_sponsorship_needed`: `no`
- `earliest_start_date`: as early as possible; typical notice is two
  weeks after offer acceptance
- `relocation_willingness`: based in Atlanta, GA and strongly prefers
  remote or local roles; open to relocation only if remote/local options
  are exhausted and the role + compensation are both exceptional
- `in_person_willingness`: remote or hybrid acceptable; fully remote
  strongly preferred
- `ai_policy_ack`: "I am transparent about my use of AI assistance in
  my work. I use AI tools (including LLMs) to accelerate drafting,
  research, and exploration, but I always keep a human in the loop: I
  review, validate, and take responsibility for all work I produce."
- `previous_interview_with_company`: `{ "anthropic": false }` (extend
  per company as history accumulates)

---

# jobpipe — unified hunt → tailor → submit pipeline

Three console scripts live in `pyproject.toml::[project.scripts]`:

| Script | Entry point | Role |
|---|---|---|
| `jobpipe-hunt`   | `jobpipe.hunt.agent:run`                         | discover roles, score, upsert |
| `jobpipe-tailor` | `jobpipe.tailor.pipeline:run_tailor_only`        | tailor resume / cover letter / form answers (no browser) |
| `jobpipe-submit` | `jobpipe.tailor.pipeline:run_submit_only`        | visible-browser pre-fill for rows the cockpit enqueued |

PR-13 split the tailor's combined cycle into two narrower entry points
so an automated trigger (CI, cron) can hit `jobpipe-tailor` without
opening a visible browser. The retired Browserbase + Stagehand runner
lives at `jobpipe/submit/runner_legacy.py` and has no console-script
binding; PR-13 reused the `jobpipe-submit` script name on purpose for
the local-Playwright pre-fill phase.

## Cross-cutting modules (canonical, no per-subtree shims after PR-9)

- `jobpipe.config` — every env-driven knob and helper. Soft defaults
  (empty string for secrets) so the module imports without credentials.
  `jobpipe.submit.config` re-promotes the submit-required secrets via
  `require_env` (fail-loud at import).
- `jobpipe.db` — Supabase data layer. Lazy module-level singleton; the
  `client` / `service_client` attributes resolve via module
  `__getattr__` so the import is side-effect-free.
- `jobpipe.notify` — Resend digest (hunt) + Supabase notifications table
  (tailor / submit). Canonical `send_*` names only — the deprecated
  `notify_*` aliases were removed once grep showed no callers.
- `jobpipe.shared.*` — `jobid`, `validator`, `html`, `storage`,
  `ats_detect`. Pure helpers used by ≥2 subtrees.

## Hunt subtree (`jobpipe/hunt/`)

Discovery + scoring + upsert. Two modes:

- `local_remote` (default): Atlanta + remote roles only.
- `us_wide`: also pulls non-remote US roles.

Sources are pure HTTP/JSON or RSS — no LLM tokens spent on discovery.
Each posting is title-pre-filtered against
`jobpipe/hunt/profile/portals.yml::title_filter` before the LLM scorer
sees it. `jobpipe.db.get_seen_ids()` deduplicates against past runs in
Supabase (PR-3 retired the JSON-state file).

## Tailor subtree (`jobpipe/tailor/`)

Polls Supabase for approved jobs, generates a tailored resume +
LaTeX-rendered PDF + cover letter + (optional) form answers, marks the
row `ready_to_submit` (or `ready_for_review` if human approval is
required). Materials live in Supabase Storage (`job-materials/{job_id}/`)
— never on disk except for ephemeral diagnostics in
`jobpipe.tailor.paths.OUTPUT_DIR`.

## Submit subtree (`jobpipe/submit/`)

**Path A is live; Path B is historical.** The only path that runs in
production is the local-Playwright pre-fill flow driven from
`jobpipe/tailor/pipeline.py` (`process_prefill_requested_jobs` /
`run_submit_watch`) through the `prepare_dom` adapters. The originally
designed Browserbase + Stagehand pipeline (`runner_legacy.py`, `router.py`,
`confirm.py`, `adapters/deterministic/*`, `adapters/generic_stagehand.py`,
`browser/session.py`) was retired during the local-Playwright consolidation
and is kept only as design reference — `confirm.py`'s per-ATS success-signal
needles and auto-submit-vs-review policy shape were the source Path A's
`page_truth.py` / verification gate ported from (P0 submit-truth-gate).
**Do not extend Path B.**

### Contract with the tailor (input)

A job is eligible when the `jobs` row has:
- `status = 'prefilling'` (the cockpit's "Pre-fill Form" click; legacy
  aliases were retired by migration 011 — canonical statuses only)
- `resume_pdf_path` + `cover_letter_pdf_path` (Storage keys)
- `cover_letter_path` — plain-text body for form-paste fields
- `application_url` / `submission_url` — resolved ATS URL to navigate to
- `materials_hash` — sha256 of resume PDF + CL text at approval time,
  enforced in Path A before every fill (`db.verify_materials_hash`, called
  from `_prefill_one_job`) — a mismatch degrades to the assisted-manual
  hand-off instead of filling with stale materials.

Pre-browser preconditions (no resume PDF, download failure, max attempts
exceeded) fail the row hard (`mark_tailor_failed`) — there's no open tab yet
to hand off. Once the tab IS open, every non-clean exit degrades to the
assisted-manual hand-off (`submit/handoff.py`) instead of a bare failure.

### Contract with the dashboard (output)

Per attempt:
- `jobs.submission_log` (jsonb): append/merge keyed by `attempt_n`, plus a
  top-level `verification` key holding the latest fill-verification summary
  (`db.record_prefill_verification` — no longer clobbers history on retry)
- `jobs.prefill_screenshot_path` / `application_notes`: the post-fill
  screenshot + "filled X of Y required field(s); still needs: ..." summary
- A row in `application_attempts`:
  - `outcome`: `prefilled` (clean pre-fill close — NOT a real ATS
    submission), `needs_review` (assisted-manual hand-off), or `failed`
  - `notes.truth` (browser-truth capture): appended once the human reaches
    a terminal decision — `{final_url, success_signal, error_signals,
    screenshot}` (`db.record_attempt_truth`)
- `jobs.status = 'applied'` — set ONLY by the human clicking "Mark Applied"
  in the cockpit. The system never auto-clicks Submit and never
  auto-sets `applied`.

### Architecture (Path A — live)

```
tailor/pipeline.py
  ├── process_prefill_requested_jobs()   one-shot cycle over status='prefilling'
  └── run_submit_watch()                 long-lived watcher (submit/watch.py:
        │                                 SubmitWatcher + Realtime/poll EventSource,
        │                                 dual-machine WatcherCoordinator)
        ▼
  _prefill_one_job(job, context)
        │  ── materials_hash gate (db.verify_materials_hash) ──
        ▼
  adapters/prepare_dom/{greenhouse,lever,ashby}.py   declarative field_maps.yml
    or  adapters/prepare_dom/universal.py            Claude tool-use fallback (no ATS map)
        │  ── run_field_map_fill / apply_field_map ──
        │     DOM re-read after every fill (a fill that didn't stick ≠ filled)
        │     scan_required_fields() unions the DOM's own required-set
        │     (custom/role-specific questions included) with field_maps.yml
        ▼
  submit/verify.py :: build_prefill_verification
        │  "filled X of Y required field(s); still needs: ..." /
        │  "N required custom question(s) unanswered"
        ▼
  required_empty == 0 ?
    yes → mark_awaiting_submit (clean)      no → submit/handoff.py
        │                                        assisted_manual_handoff
        │                                        (tab open, materials staged
        │                                         locally, checklist written)
        └──────────────┬─────────────────────────────┘
                        ▼
        BOTH paths land on jobs.status = 'awaiting_human_submit' — tab
        stays open, human reviews and clicks Submit themselves
                        │
                        ▼
        _wait_for_human_decision(page, job_id, attempt_id, ats, job)
          polls jobs.status until a terminal decision (applied / skipped /
          failed / expired) or a JOBPIPE_DECISION_TIMEOUT_MINUTES timeout
          (re-queues to 'prefilling' on timeout — submit/watch.py's
          in-flight dedup clears automatically since this call returning
          is what frees it)
                        │
                        ▼
        submit/page_truth.py :: capture_truth(page, ats)
          success-signal needles (ported from the retired confirm.py) +
          a generic validation-error DOM scan → final URL + screenshot +
          signals, appended to application_attempts.notes.truth
          (db.record_attempt_truth). Mismatch (marked applied, no success
          signal, errors visible) → notify.send_truth_mismatch — loud, but
          jobs.status is NEVER touched here; the human stays authoritative.
```

### Design rules

- **Adapters fill. The human clicks Submit. Nothing auto-submits, ever.**
  Every non-clean prepare exit degrades to the assisted-manual hand-off
  (tab left open, materials staged, checklist written) rather than a bare
  failure or an auto-retry.
- **Honest verification, not adapter self-report.** A selector matching
  and a Playwright `.fill()` call not raising is not proof a value stuck
  (React forms silently discard fills). `apply_field_map` re-reads the DOM
  after every fill; the required-set denominator comes from the form
  itself (`scan_required_fields`), not just the ~5 hardcoded
  `field_maps.yml` labels, so custom/role-specific questions the ATS marks
  required can't hide behind "filled 5 of 5."
- **Browser-truth capture is evidence, not a gate.** The post-decision
  success/error DOM probe (`page_truth.py`) informs a mismatch
  notification; it never flips `jobs.status` automatically. The human's
  click stays the single source of truth for whether an application was
  actually submitted.
- **One local Playwright browser context per run/watcher lifetime**, one
  tab per job, strictly serial (the human reviews one form at a time).
- **LLM use is bounded** to the `universal` (no-ATS-map) prepare fallback
  and the M-1 `form_answers` generation upstream in the tailor. The
  deterministic `prepare_dom` adapters use zero LLM calls.
- **Every state transition writes a row to `application_attempts`.** Never
  mark a row `awaiting_human_submit` / `applied` without a corresponding
  attempts row carrying the evidence.

**Path B (historical — do not extend).** `runner_legacy.py`, `router.py`,
`confirm.py`, `adapters/deterministic/*`, `adapters/generic_stagehand.py`,
and `browser/session.py`'s Browserbase+Stagehand session were the
originally designed remote-browser submission pipeline, retired dead code
kept only as reference. If a future remote-browser fallback is ever built,
start from these files' ideas; do not wire them back into the live path
as-is.

## Where the data lives

- `jobs` table — main pipeline row (hunt writes; tailor / submit
  transition status)
- `application_attempts` — audit trail per submit attempt
- `notifications` — written by `jobpipe.notify`
- `star_stories` — interview prep stories (written by tailor's
  `interview_prep` module)
- `pattern_analyses` — closed-loop pattern analysis output
  (`jobpipe/tailor/scripts/analyze_patterns.py`)
- Supabase Storage `job-materials/{job_id}/` — resume.pdf,
  cover_letter.pdf, prefill.png, review/*.png
