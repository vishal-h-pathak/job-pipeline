# STAR+R Story Generator

Generate **3 to 5 STAR+R interview stories** for Vishal Pathak grounded
in his actual profile and tailored to this specific job posting. Each
story must answer a behavioral question a hiring manager would plausibly
ask for THIS role.

The +R is *Reflection* — what he learned, what he'd do differently. It
turns a generic STAR into a story the interviewer remembers.

CANDIDATE PROFILE:
{profile}

CHOSEN ARCHETYPE (lean these stories toward this lane):
{archetype_block}

JOB POSTING:
Title: {job_title}
Company: {company}
Description: {job_desc}

GROUND RULES:

1. **Only stories grounded in real experience.** Pull from GTRI projects
   (SPARSE, 360-SA, HACS, GREMLIN, ENFIRE, DRAGON, PAAM, SHELAC), Rain
   Neuromorphics, FlyGym/connectomics work, the trading + job-hunter
   agent projects. Do NOT invent companies, projects, dates, or metrics.
2. **Reflection must be honest.** It's the highest-trust part of the
   story — what he learned, what he'd do differently, what surprised
   him. Avoid faux-humility ("I learned to be a better team player").
   Specific reflections only: "I'd benchmark VHDL behavior against the
   LavaSDK reference simulator earlier — would have caught the
   off-by-one timing bug a month sooner."
3. **Tags should be searchable.** Include archetype, key skills/tools
   surfaced (e.g. "vhdl", "loihi", "snn", "playwright", "supabase"),
   and behavioral-question category ("conflict", "ambiguity",
   "leadership-without-authority", "deadline-pressure").
4. **Each story ~80-150 words across STAR fields.** Reflection 30-60
   words.
5. **Output strict JSON only.** No prose, no code fences.

Respond with a JSON object of the form:

```
{{
  "stories": [
    {{
      "situation": "...",
      "task": "...",
      "action": "...",
      "result": "...",
      "reflection": "...",
      "tags": ["...", "..."]
    }}
  ]
}}
```
