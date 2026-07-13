# ATS Keyword Gate + Humanizer

You are running a post-tailor QA pass on a resume that has already been
generated for a specific job posting. Two jobs in one call:

1. **ATS keyword gate** — score how well the resume's language covers
   the JD's actual keyword surface, and name the single highest-impact
   fix.
2. **Humanizer** — flag any bullet that reads as AI-generated / robotic
   and propose a de-AI rewrite.

CONSTRAINTS (binding — the Honesty, Anti-slop, and Voice rules in the
system prompt apply to every rewrite you propose):

- Never fabricate experience, skills, tools, or metrics. A
  `humanized_rewrite` may only rephrase what the original bullet already
  claims — it must not add a tool, number, or outcome that wasn't in the
  `bullet` text you were given.
- Every real number in the original bullet (metrics, percentages,
  counts, dates) must survive unchanged into the rewrite.
- Vary sentence length and structure across rewrites — don't produce a
  uniform cadence; that itself reads as AI-generated.
- Match Vishal's voice profile (system prompt) — conversational,
  technically precise, mechanism over adjectives, no corporate filler.
- `highest_impact_fix` is ONE sentence naming the single most valuable
  change — not a checklist. Pick the fix that would move the ats_score
  the most.

JOB POSTING (the JD to score against):
Title: {job_title}
Company: {company}
Description:
{job_desc}

RENDERED RESUME (the tailored resume text that was actually produced
for this JD):
{resume_text}

Respond with ONLY a JSON object (no prose, no code fences) of the form:

```
{{
  "top_keywords": ["<up to 15 ranked JD keywords/phrases, most important first>"],
  "missing": ["<subset of top_keywords that the resume text does not cover>"],
  "ats_score": <int 0-100>,
  "highest_impact_fix": "<one sentence>",
  "robotic_bullets": [
    {{"bullet": "<verbatim bullet from the resume text>", "humanized_rewrite": "<de-AI rewrite, same facts>"}}
  ]
}}
```

If no bullets read as robotic, return `"robotic_bullets": []`. If the
resume already covers every top keyword, return `"missing": []`.
