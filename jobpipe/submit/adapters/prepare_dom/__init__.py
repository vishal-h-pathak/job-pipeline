"""jobpipe.submit.adapters.prepare_dom — M-4 prepare-only DOM fillers.

Per-ATS DOM-based form fillers that read from ``job["form_answers"]`` and
fill standard fields via Playwright. Zero Anthropic API calls. The
handlers NEVER click Submit — the orchestrator (in
``jobpipe.tailor.pipeline.process_prefill_requested_jobs``) takes a
post-fill screenshot, marks the row ``awaiting_human_submit``, and blocks
on a terminal ``input()`` while the human reviews the visible browser.

These adapters were moved from the legacy ``jobpipe.tailor.applicant``
package in PR-4. They live here because they share the
``submit/adapters/`` namespace with the M-3 deterministic submitters in
``submit/adapters/deterministic/`` — both serve the post-tailor phase of
the pipeline. PR-7 will factor shared helpers up into
``submit/adapters/_common.py``.
"""
