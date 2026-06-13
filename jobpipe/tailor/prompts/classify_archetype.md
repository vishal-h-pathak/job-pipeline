# Classify Archetype

You are routing a job posting to the best-fit candidate "archetype" for
Vishal Pathak. Archetypes are different lanes the same candidate can
be framed as — pick the one whose framing, emphasis points, and tone
most closely match this specific JD.

The CANDIDATE PROFILE — including the canonical hunting thesis
(thesis.md, FIRST document, wins on conflict) whose tier semantics bind
this routing decision — is in the system prompt.

The archetypes available are listed below with their framings. You must
return ONE archetype key. If the JD straddles two archetypes, pick the
one with stronger evidence. If the JD doesn't fit any archetype,
return `tier_3_mission_ml` as the fallback (mission-driven ML/CV is
the broadest lane).

ROUTING RULE — `tier_1_5_agentic_builder`: pick this archetype when the
job's tier is 1.5, or when the JD is centrally about building LLM-agent
systems, applied-AI products, agent infrastructure, or forward-deployed
engineering — even if the scorer tiered the job differently. "Centrally
about" means the day-to-day work is building/operating agent systems,
not a role that merely mentions AI tooling in passing.

ARCHETYPE OPTIONS:
{archetypes_block}

JOB POSTING:
Title: {job_title}
Company: {company}
Tier: {tier}
Description: {job_desc}

Respond with ONLY a JSON object (no prose, no code fences) of the form:

```
{{
  "archetype": "<one of the archetype keys above>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence on why>"
}}
```
