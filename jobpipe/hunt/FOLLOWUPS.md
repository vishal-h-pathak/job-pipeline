# jobpipe-hunt: ATS support follow-ups

Tracks dead portals from `portals.yml` that this audit could not auto-recover
because the company moved to an ATS we don't yet scrape, chose a non-API
careers flow, or disabled their ATS's public posting API. Re-poll quarterly;
promote to a `sources/<ats>.py` follow-up if two or more Tier-1/Tier-2 targets
end up on the same ATS.

## Unrecovered portals

| Company | ATS | Tier | URL pattern | Feasibility |
|---|---|---|---|---|
| Synchron | ADP WorkforceNow | T1 (BCI) | `workforcenow.adp.com/mascsr/default/mdf/recwebcomponents/recruitment/` (cid=`d290c04e-0230-4cd9-8bf0-f116bfab1405`) | Has a public `recruitment.js` widget; feasible but non-trivial. ADP is large enough that supporting it would unlock other companies too. |
| Paradromics | ApplyToJob (Resumator) | T1 (BCI) | `paradromicsinc.applytojob.com/apply` | ApplyToJob exposes a public listing API (`/jobs.json`); straightforward to scrape. |
| Cortical Labs | custom interest form | T1 (biocomputing) | `corticallabs.com/careers.html` (no listings, just an email/interest form) | Not worth a scraper. Manual quarterly check or LinkedIn-watch. |
| Replicate | Ashby (private posting API) | T2 (sales-eng) | `jobs.ashbyhq.com/replicate` exists but `api.ashbyhq.com/posting-api/job-board/replicate` returns 404 | Not fixable from our side; company has to flip board visibility in Ashby admin. Re-poll quarterly. |
| Groq | Gem | T2/T3 | `jobs.gem.com/groq` | Gem appears to expose a JSON API. Worth a follow-up if more Gem targets accumulate. |
| Sakana AI | Google Forms | T3 | Google Form + email (`careers@sakana.ai`) | Not worth a scraper. |

## Per-portal title-filter override (open question)

After the W&B → CoreWeave consolidation (`weightsandbiases` → `coreweave`), the
Greenhouse board jumped from ~30-50 W&B-only jobs to **244 jobs**, mixing
ML/W&B-relevant roles with cloud-ops, sales, legal, and finance. The current
global `title_filter` is intentionally permissive ("rather over-LLM-score than
miss a Tier 1 role"), so ~5x more candidates would reach the LLM scorer than
before, with corresponding token spend.

Proposed follow-up: add a per-portal `include_substrings` mechanism in
`portals.yml` so individual high-volume / off-mission boards can require at
least one of a substring whitelist before the LLM is consulted. For CoreWeave
that list might be `["w&b", "wandb", "weights & biases", "ml", "machine
learning", "ai", "research", "infrastructure", "applied scientist"]`.

Currently un-implemented; tracked here so it surfaces on the next audit.

## Audit history

- 2026-05-03 — initial audit (PROMPT_audit_portals.md). 18 dead portals
  triaged; 13 auto-recovered via slug fix or supported-ATS migration; 6
  surfaced here.
