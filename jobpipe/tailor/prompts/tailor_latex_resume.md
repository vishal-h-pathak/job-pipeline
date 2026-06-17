# Tailor LaTeX Resume

You are tailoring a LaTeX resume for Vishal Pathak for a specific job
application. His real, professional experience is the SPINE of the resume.
Your job is to SELECT and REORDER content to best match the target role, and
to REFRAME the same real bullets in the listing's language. You may rewrite
bullet points to emphasize relevant aspects, but you MUST NOT fabricate
experience, skills, or projects he doesn't have.

The CANDIDATE PROFILE (thesis.md canonical-first) and VOICE PROFILE are
in the system prompt.

BASE RESUME DATA (this is the complete truth — the professional employers,
programs, and bullets available; this is ALL the professional experience):
{base_resume_json}

TAILORING GUIDANCE (from earlier analysis):
{tailoring_json}

TARGET JOB:
Title: {job_title}
Company: {company}
Description: {job_desc}
{match_chat_block}

CHOSEN ARCHETYPE (J-4 — bias selection + emphasis toward this lane. Same
candidate, different framing):
{archetype_block}

{project_bank_block}

{honesty_block}

## THE TWO REFRAMING LEVERS

You have exactly two levers. Pull both; invent nothing.

1. **SELECTION** — Treat the GTRI professional experience as the spine. From
   GTRI's programs, select the ones most relevant to THIS listing and order
   them by relevance (most relevant first). Rain is always included. If a
   PROJECT BANK was provided above, also select the 2-3 most relevant projects
   from it (and only from it). If no bank was provided, surface NO projects.

2. **EMPHASIS** — Reword the *same real bullets* into the listing's language,
   drawing on the rich technical detail in the base data. The same SPARSE work
   is "neuromorphic hardware / FPGA / VHDL neuron models" for a chip role, or
   "embedded ML systems deployed to edge accelerators" for an ML role — same
   facts, different vocabulary. Reframe; never fabricate. Do not round up
   status or scope.

## YOUR TASK — respond with a JSON object containing:

1. "skills" — a dict of 4-5 skill categories with comma-separated skills.
   Rewrite category names and reorder skills to lead with what's most relevant.
   Only include skills he actually has from the base data.

   You have flexibility on category names — the resume's two-column
   skills layout auto-sizes the left column to fit the longest label
   you pick (up to ~32 characters), so you don't need to artificially
   compress descriptive names. That said, terse labels (1–3 words)
   read better at a glance; use longer phrasing only when the extra
   words actually help frame the skills for this role.

2. "skills_layout" (optional) — one of "auto" (default), "compact",
   "wide", or "stacked". Leave it out (or set to "auto") in almost all
   cases — the renderer will pick a width that fits your labels.
   - "compact" forces the original tight 4.5cm left column. Pick this
     only when you've deliberately chosen short labels and want a
     wider value column.
   - "wide" forces the maximum 7.0cm two-column layout. Useful if
     your labels are right at the boundary and you want to err on
     the side of not wrapping.
   - "stacked" puts each category label on its own line above its
     skills value. Reach for this only if you've intentionally
     chosen very long descriptive labels (>32 chars) or have many
     categories where readability suffers in a table.

3. "experience" — a list of PROFESSIONAL experience entries ONLY. Each entry has:
   - "org", "title", "location", "period" (keep these factual, copy verbatim
     from the base data — they are already LaTeX-safe)
   - "projects" — the GTRI programs (and Rain) to INCLUDE (drop irrelevant ones).
     Each has "name" (null for Rain), "period", and "bullets". You may rewrite
     bullets to emphasize relevant aspects, but keep them factual.
     Lead with the most relevant programs for this role.
   - The "experience" list MUST contain ONLY employers present in the base data
     (GTRI, Rain). NEVER create a "Personal Projects" entry or any employer not
     in the base data. Personal projects go ONLY in the separate "projects" key
     below — never as an experience entry.

4. "projects" — a list of personal projects to surface, drawn ONLY from the
   PROJECT BANK provided above. If no bank was provided, return [].
   Each project is a single tight line:
   - "name" — the project's name (from the bank)
   - "description" — a reframed one-line description in the listing's language,
     based on the bank's one_liner; keep the honest status intact (e.g.
     MERIDIAN is paper trading; agentic projects are "built/operate solo" or
     WIP; papercuts is genuinely shipped with real members).
   Pick the 2-3 most relevant; this is a SMALL section, never more than a few
   lines, and it must never crowd out professional experience.

5. "summary_line" — optional 1-line summary to add below the header (or null to skip).
   If included, write it in Vishal's voice: direct, technical, no fluff.

ONE PAGE IS MANDATORY. The resume MUST fit on a single page. Professional
experience is the bulk of every resume; projects are a small, conditional
section (or absent). Budget the content so it fits — a downstream trim loop
will mechanically drop personal projects FIRST, then professional bullets, if
you overflow, but it can only cut, so anything past these caps just gets
deleted. Target one page directly:
- Show at most 3 experience entries (programs) total across all employers,
  ordered most-relevant first.
- At most 4 bullets per entry.
- At most 3 personal projects (only if a bank was provided), one line each.
- Each bullet ≤ 2 printed lines (roughly ≤ 200 characters).
- The optional "summary_line" is ≤ 2 lines, or null (omit it when in doubt).
- Keep skills compact: 4-5 categories, comma-separated.

RULES:
- GTRI programs you can include or exclude based on relevance. Always include at least
  SPARSE and one other. Drop programs that add no value for this specific role.
- Rain Neuromorphics should always be included.
- The EMPHASIS PROOF POINTS in the archetype block may name personal projects —
  use them ONLY to guide emphasis and wording. Personal projects appear on the
  resume ONLY through the PROJECT BANK and the "projects" key, never as an
  experience entry.
- Rewrite skill categories to match the job posting's language where honest.
- Bullets should be specific and technical. No vague claims.
- Keep the resume to 1 page worth of content — see the mandatory caps above.
- Do NOT add programs, employers, skills, or projects that don't exist in the
  base data / project bank.

Respond with valid JSON only, no markdown.
