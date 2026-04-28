# Classify Archetype

You are routing a job posting to the best-fit candidate "archetype" for
Vishal Pathak. Archetypes are different lanes the same candidate can
be framed as — pick the one whose framing, emphasis points, and tone
most closely match this specific JD.

The archetypes available are listed below with their framings. You must
return ONE archetype key. If the JD straddles two archetypes, pick the
one with stronger evidence. If the JD doesn't fit any archetype,
return `tier_3_mission_ml` as the fallback (mission-driven ML/CV is
the broadest lane).

ARCHETYPE OPTIONS:
{archetypes_block}

JOB POSTING:
Title: {job_title}
Company: {company}
Description: {job_desc}

Respond with ONLY a JSON object (no prose, no code fences) of the form:

```
{{
  "archetype": "<one of the archetype keys above>",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<one sentence on why>"
}}
```
