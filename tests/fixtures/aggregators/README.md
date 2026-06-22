# Aggregator extraction fixtures

Test inputs for `jobpipe.tailor.url_resolver._extract_ats_link_from_html`.

## Real captures (trimmed live HTML, fetched 2026-06-15, HTTP-only/zero-token)

| Fixture | Source | Strategy it exercises | Expected |
|---|---|---|---|
| `teal_greenhouse.html` | tealhq.com posting | embedded inline JSON (`window.__REACT_QUERY_STATE__`, `"url"` field) | greenhouse URL |
| `teal_workday.html` | tealhq.com posting | embedded inline JSON | myworkdayjobs URL |
| `teal_icims.html` | tealhq.com posting | embedded inline JSON | icims URL |
| `teal_smartrecruiters.html` | tealhq.com posting | embedded inline JSON | smartrecruiters URL |
| `talent_onsite.html` | talent.com posting | JSON-LD JobPosting with **no** `url` + on-site apply anchor | `None` (stays flagged) |

The teal blobs are windowed to ~600 chars around the real `"url"` field to
keep the fixtures lean; the structure (inline `<script>` holding a JSON object
whose `url` points at the ATS) is exactly as served.

## Representative fixtures (standard structures; live pages unreachable here)

simplify.jobs refuses our httpx client at the TLS layer
(`TLSV1_ALERT_PROTOCOL_VERSION`); jooble.org and learn4good.com return 403 to a
bare HTTP client. Their real apply-link DOM could not be captured from this
environment, so these two fixtures encode the *standard* shapes the extractor
must also handle (schema.org JobPosting is a W3C spec, not a guess):

| Fixture | Strategy it exercises | Expected |
|---|---|---|
| `jsonld_jobposting.html` | schema.org JSON-LD `JobPosting.url` | ashby URL |
| `anchor_apply.html` | anchor / `data-*` attr pointing at an ATS host | lever URL |

## Source-URL extraction fixtures (`_extract_source_url_from_html`)

These exercise recovery of the canonical *source* posting URL even when it is
**not** on an ATS host — the input to the resolver's recursion (TealHQ →
careers.qualcomm.com → the real ATS form a hop later). Distinct from the ATS
extractor above, which only matches known-ATS hosts.

| Fixture | Strategy it exercises | Expected |
|---|---|---|
| `teal_nextdata_qualcomm.html` | Next.js `<script id="__NEXT_DATA__">` `url` field → non-ATS careers host | `careers.qualcomm.com/...` |
| `jsonld_source_careers.html` | schema.org JSON-LD `JobPosting.url` → non-ATS careers host | `careers.acme.com/...` |
