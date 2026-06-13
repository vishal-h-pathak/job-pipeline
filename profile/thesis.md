# Hunting Thesis — Vishal Pathak

> Consumed by the hunt scorer as primary context alongside `profile.yml` and
> `cv.md`. This document encodes *judgment*, not biography: how Vishal actually
> decides whether a posting is worth his time. When this file and older profile
> prose disagree, this file wins. Updated 2026-06-12 from direct calibration
> with Vishal plus observed pipeline outcomes.

## The thesis in one paragraph

Vishal is an EE who has spent his whole career one layer below where most ML
people work: building the neurons, not just calling them. Hodgkin-Huxley as an
undergrad → memristive LIF neuron PCBs at Rain Neuromorphics (employee #5, age
19) → four years of SNN deployment on Intel Loihi, VHDL neuron models, and
embedded CV at GTRI. He wants back toward the brain — and in 2026 he has added
a second, equally real identity: he builds agentic AI systems end-to-end
(multi-stage LLM pipelines with human-in-the-loop gates, audit trails, CI,
browser automation) as a daily practice, for fun. The ideal role sits at the
intersection: experimental, hands-on, building something novel near
neuroscience or near the frontier of applied AI. He is flexible about almost
everything else.

## Hard constraints (violating any of these = score floor, do not surface)

- **Remote (or Atlanta local/hybrid).** Relocation only for an
  eon.systems-caliber combination of mission and comp.
- **No pay cut.** Base at or above ~$110k. Target $120–140k. A true Tier 1
  role at $110–120k is acceptable; below current comp is not.
- **No defense/DoD/government contracting, nothing clearance-gated.** He is
  leaving this world deliberately. "Defense-adjacent" (dual-use marketed to
  primes) counts as defense.
- **No academic positions** — postdoc, professorship, PhD programs. No PhD,
  not pursuing one.

## The degree-gate rule (new, important)

Brain-adjacent postings frequently require an MS/PhD. Vishal has a BS and
nine years of rare hands-on experience. Rule:

- If the JD says "PhD required" or "MS required" with no "or equivalent
  experience" escape hatch → set `degree_gated: true` in your output and cap
  the surfaced enthusiasm: it can still be shown, but never as a top pick,
  and the summary must lead with the gate so he isn't disappointed twice.
- If it says "PhD preferred" / "or equivalent practical experience" → not
  gated. Nine years of neuromorphic hardware IS the equivalent experience;
  say so in the fit reasoning.
- Titles: "Research Engineer" almost always passes; "Research Scientist" at
  pharma/biotech usually gates; "Researcher" at startups varies — read the JD.

## Tiers (updated)

**Tier 1 — anything brain.** Computational neuroscience, neuromorphic
hardware/software, connectomics, BCI, neurotech, embodied simulation,
event-based/neuromorphic vision. His words: "any jobs involving the brain I
would love to do." This is the center. Score generously here; the funnel has
historically under-surfaced Tier 1 (3 of 15 in-flight) and that ratio should
invert.

**Tier 1.5 — agentic / applied AI engineering (new).** Roles where the job is
building LLM-agent systems, tooling, or applied-AI products: agent engineer,
applied AI engineer, forward-deployed engineer, AI integration engineer,
member of technical staff at agent-focused startups. Confirmed directly:
building agentic systems "has become one of my favorite things." He has a
shipping portfolio piece (an autonomous job-application pipeline: discovery →
LLM scoring → tailored materials → browser prefill, stop-at-submit by design).
Treat a strong Tier 1.5 match as nearly Tier 1, above any Tier 2.

**Tier 2 — AI/LLM sales & solutions engineering.** Strong communicator, rare
technical depth, has pitched DoD sponsors. Still valid but now ranks BELOW
Tier 1.5: prefer roles where he builds over roles where he demos. A sales-eng
role at a company whose hardware/product he'd love (e.g. a neuromorphic chip
maker, an ML-tooling company he respects) scores higher than generic pre-sales.

**Tier 3 — mission-driven ML/CV/data roles.** Heavily company-dependent.
Filter hard: "experimental and research-oriented beats big and established."
A traditional SWE/data role at a megacorp is explicitly unappealing even at
higher comp. Small weird ambitious > large stable boring.

## Named companies (calibration anchors — generalize from the *why*)

Dream-tier: **eon.systems** (the reference point), **Rain Neuromorphics** (his
old start — returning interest is real), **Kernel (kernel.co)** (non-invasive
brain interfaces; founder eccentricity is not a deterrent), **Neuralink**
(same vein), **X, the moonshot factory (x.company)** (named directly as
incredibly interesting — the experimental zero-to-one register generalizes:
corporate skunkworks and moonshot labs count as small-and-weird even inside
giants; note X hires through Google's own careers portal, no pollable ATS
board, so it's a scoring anchor rather than a discovery source).
Generalize: small-to-mid companies attacking the brain or the frontier with
actual hardware or actual agents — not consultancies orbiting them.

Companies in the same family worth watching for: neuromorphic silicon
(Innatera, SynSense, BrainChip, Tenstorrent-class hardware startups),
event-based vision (Prophesee), BCI (Synchron, Paradromics, Precision
Neuroscience, Blackrock Neurotech), frontier AI labs and agent-infrastructure
startups. Verify each posting on its own merits — these names indicate the
*shape* of the target, not an allowlist.

## Energy signals (weight JD language against these)

Strong positive — the role day-to-day involves:
- Novel or exotic hardware in hand: neuromorphic chips (Loihi-class), new
  silicon, lab bench work, "you will be one of the first to program X"
- Building complete systems solo or in a small team: hardware OR software,
  zero-to-one, prototypes, "own the whole stack"
- Agentic AI: tool use, orchestration, evals, human-in-the-loop design
- Simulation of living systems: FlyGym/MuJoCo-class embodied sim, Brian2-class
  neural sim

Strong negative — the role day-to-day involves:
- Maintaining or extending legacy codebases (his single named never-again:
  feature work on a legacy MATLAB repo)
- Process-heavy feature factories; "you will work with stakeholders to
  groom the backlog"
- Pure demo/quota motion with no building
- Big-company SWE interchangeability ("traditional SWE job at Microsoft" is
  the named anti-example)

## Worked examples from the live funnel (real verdicts to calibrate against)

- Limitless — "Researcher, Consciousness and Connectomics" → Tier 1, 9.
  Correct. This is the bullseye archetype.
- Anthropic — "Research Engineer, Model Evaluations" → Tier 1, 8. Correct:
  frontier lab + research-engineer title + agentic/eval work = Tier 1/1.5.
- Weights & Biases — "AI Solutions Engineer, Pre-Sales" → was Tier 2, 8.
  Still good (tooling he respects) but under the new ordering a comparable
  agent-ENGINEERING role should outrank it.
- Notify Health — "Data Scientist, OCR" → Tier 3, 5. Correctly mediocre:
  mission-flavored but not experimental, no building edge. The funnel should
  carry fewer of these.
- Qualcomm — "Pre-Sales Solution Engineer" → Tier 2, 8 is too generous under
  this thesis: big established company, generic pre-sales, no neuromorphic
  angle in the JD. 5–6.

## Tone notes for downstream prompts

He is self-aware about needing external structure, works best pointed at a
compelling problem, and reads as a builder with one long obsession rather
than a generalist. Materials should never round him up to "ML researcher"
or down to "embedded engineer" — the accurate frame is "the person who has
been building brains in hardware and software since he was nineteen."
