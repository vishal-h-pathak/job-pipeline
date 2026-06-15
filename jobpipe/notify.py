"""jobpipe.notify — canonical notification layer for the whole pipeline.

PR-8 consolidates ``jobpipe/hunt/notifier.py`` (Resend HTML-email
digest) and ``jobpipe/tailor/notify.py`` (Supabase ``notifications``
table writes) into this single module. Per-subtree files become thin
shims so the unprefixed-import pattern PR-3/4/5 set up keeps working.

Two notification surfaces — kept side-by-side, not merged:
    1. Hunt's daily / on-discovery email digest, sent via Resend. Used
       by the legacy email path; the dashboard subsumes most of its
       role but the digest is still the heartbeat alert.
    2. The tailor / submit pipeline's per-job ``notifications`` table
       writes consumed by the cockpit at ``vishal.pa.thak.io``. Each
       row carries a deep link back to ``/dashboard/review/{job_id}``.

Naming policy (PR-8):
    - Canonical names use the ``send_*`` prefix per the consolidation
      spec: :func:`send_digest`, :func:`send_awaiting_review`,
      :func:`send_awaiting_submit`, :func:`send_applied`,
      :func:`send_failed`.
    - The pre-PR-8 ``notify_*`` names survived as deprecated aliases
      until Session C verified no callers remained and removed them.
    - **External-facing strings are decoupled from the rename.** The
      ``notification.type`` field written here stays ``"ready_for_review"``
      / ``"awaiting_human_submit"`` because (a) those values are part of
      the cockpit's contract for the notifications panel, and (b) the
      ``jobs.status`` CHECK enum (migration 007) uses the same strings.
      Renaming the symbol does not propagate to user-visible text.
"""

from __future__ import annotations

import html
import logging
import os
from datetime import datetime, timezone
from typing import Union

import requests

from jobpipe.config import (
    PORTFOLIO_BASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger("jobpipe.notify")


# ══════════════════════════════════════════════════════════════════════════
#  Hunt — Resend HTML email digest (was jobpipe/hunt/notifier.py)
# ══════════════════════════════════════════════════════════════════════════

RESEND_URL = "https://api.resend.com/emails"
FROM_ADDR = os.environ.get("NOTIFY_FROM", "Job Agent <jobs@vishal.pa.thak.io>")
TO_ADDR = os.environ.get("NOTIFY_TO", "vishal@pa.thak.io")


def _tier_key(tier):
    """Sort/display key for a digest tier section; 99 = unknown bucket.

    Handles the thesis's half tier: 1.5 (or "1.5") sorts between 1 and
    2 and renders as "Tier 1.5". Whole tiers normalize to int so their
    headers stay "Tier 1", not "Tier 1.0".
    """
    if isinstance(tier, bool):
        return 99
    try:
        f = float(tier)
    except (TypeError, ValueError):
        return 99
    return int(f) if f.is_integer() else f


def _apply_link(job: dict) -> str:
    """Prefer the resolved ATS URL over the raw (often aggregator) URL so
    the digest links straight to the real application page when the hunt
    gate resolved one."""
    return job.get("application_url") or job.get("url") or ""


def _unverified_flag(job: dict) -> str:
    """A visible ⚠ tag for rows the hunt gate couldn't resolve to a direct
    ATS (link_status='aggregator_unverified'), so direct vs aggregated is
    legible at a glance. Empty for direct / unclassified rows."""
    if job.get("link_status") != "aggregator_unverified":
        return ""
    return (
        '<span style="display:inline-block; border:1px solid #d9a300; '
        'background:#fff8e1; color:#8a6d00; border-radius:10px; '
        'padding:1px 8px; font-size:12px; margin-left:8px;">'
        "⚠ unverified link</span>"
    )


def _render_job(job: dict, score: dict) -> str:
    return f"""
    <div style="border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px;
                margin-bottom: 14px;">
      <h3 style="margin: 0 0 4px 0;">{html.escape(job['title'])}{_unverified_flag(job)}</h3>
      <div style="color: #555; margin-bottom: 8px;">
        {html.escape(job['company'])} · {html.escape(job['location'])}
        &nbsp;·&nbsp; <strong>{score.get('score')}/10</strong>
      </div>
      <p style="line-height: 1.5; margin: 8px 0;">
        {html.escape(score.get('reasoning', ''))}
      </p>
      <p style="margin: 8px 0 0 0;">
        <a href="{html.escape(_apply_link(job))}"
           style="display: inline-block; padding: 8px 14px; background: #111;
                  color: #fff; text-decoration: none; border-radius: 6px;">
          View &amp; Apply →
        </a>
        <span style="color: #888; font-size: 12px; margin-left: 10px;">
          source: {html.escape(job.get('source', '?'))}
        </span>
      </p>
    </div>
    """


def _render_digest(entries: list[dict]) -> tuple[str, str]:
    by_tier: dict[int, list[dict]] = {}
    for e in entries:
        by_tier.setdefault(_tier_key(e["score"].get("tier")), []).append(e)
    for tier_entries in by_tier.values():
        tier_entries.sort(key=lambda e: e["score"].get("score", 0), reverse=True)

    sections = []
    for tier in sorted(by_tier.keys()):
        label = f"Tier {tier}" if tier != 99 else "Other"
        cards = "".join(_render_job(e["job"], e["score"]) for e in by_tier[tier])
        sections.append(
            f'<h2 style="margin: 24px 0 10px 0;">{label} '
            f'<span style="color:#888;font-weight:normal;">'
            f'({len(by_tier[tier])})</span></h2>{cards}'
        )

    subject = f"Job digest: {len(entries)} new match{'es' if len(entries) != 1 else ''}"
    body = (
        '<div style="font-family: -apple-system, system-ui, sans-serif; '
        'max-width: 640px;">'
        + "".join(sections)
        + "</div>"
    )
    return subject, body


def send_digest(entries: list[dict]) -> bool:
    """Send the hunter's HTML email digest via Resend.

    ``entries`` is a list of ``{"job": job_dict, "score": score_dict}``
    pairs. Returns False (without raising) if no jobs to notify or if
    ``RESEND_API_KEY`` is unset, so a missing-secret environment falls
    back to a console line rather than crashing the hunter loop.
    """
    if not entries:
        print("[notifier] no jobs to notify")
        return False
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print(f"[notifier] RESEND_API_KEY not set; would digest {len(entries)} jobs")
        return False
    subject, html_body = _render_digest(entries)
    resp = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_ADDR,
            "to": [TO_ADDR],
            "subject": subject,
            "html": html_body,
        },
        timeout=20,
    )
    if resp.status_code >= 300:
        print(f"[notifier] resend failed {resp.status_code}: {resp.text}")
        return False
    return True


# ── Morning digest (Session I) ─────────────────────────────────────────────
# A second, narrower Resend surface: the top-N freshly-hunted jobs worth a
# look, sent by hunt.yml's cron path right after the hunt completes. The
# caller (scripts/send_morning_digest.py) queries the rows (status='new',
# created in the last 24h); this function applies the score bar, sorts,
# caps, renders, and sends. Empty result → no email at all (no noise).

MORNING_DIGEST_SCORE_BAR = 7
MORNING_DIGEST_TOP_N = 5


def _one_line(text: str, limit: int = 180) -> str:
    """Collapse a reasoning blob to a single ≤limit-char line."""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    return t[:limit].rsplit(" ", 1)[0] + "..."


def _tier_pill(tier) -> str:
    key = _tier_key(tier)
    label = f"Tier {key}" if key != 99 else "Tier ?"
    return (
        '<span style="display:inline-block; border:1px solid #ccc; '
        'border-radius:10px; padding:1px 8px; font-size:12px; '
        f'color:#333;">{html.escape(label)}</span>'
    )


def _render_morning_digest(top: list[dict]) -> tuple[str, str]:
    n = len(top)
    subject = f"hunt digest — {n} worth a look"
    cards = []
    for job in top:
        review_url = cockpit_url(job.get("id"))
        cards.append(
            '<div style="margin: 0 0 18px 0;">'
            f'<div><strong>{html.escape(job.get("title") or "?")}</strong>'
            f' — {html.escape(job.get("company") or "?")}{_unverified_flag(job)}</div>'
            f'<div style="color:#555; margin:2px 0;">{_tier_pill(job.get("tier"))}'
            f' &nbsp;<strong>{job.get("score")}/10</strong></div>'
            f'<p style="line-height:1.5; margin:6px 0;">'
            f'{html.escape(_one_line(job.get("reasoning")))}</p>'
            f'<a href="{html.escape(review_url)}">Review &rarr;</a>'
            "</div>"
        )
    body = (
        '<div style="font-family: -apple-system, system-ui, sans-serif; '
        'max-width: 640px;">'
        f'<p style="color:#555;">New in the last 24h, score &ge; '
        f"{MORNING_DIGEST_SCORE_BAR}:</p>"
        + "".join(cards)
        + "</div>"
    )
    return subject, body


def select_morning_digest(
    jobs: list[dict], top_n: int = MORNING_DIGEST_TOP_N
) -> list[dict]:
    """Apply the digest policy: score ≥ bar, sorted desc, capped at top_n."""
    candidates = []
    for job in jobs or []:
        try:
            score = int(job.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score >= MORNING_DIGEST_SCORE_BAR:
            candidates.append((score, job))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [job for _, job in candidates[:top_n]]


def send_morning_digest(
    jobs: list[dict],
    top_n: int = MORNING_DIGEST_TOP_N,
    subject_prefix: str = "",
) -> bool:
    """Send the morning hunt digest via Resend.

    ``jobs`` are candidate rows (the caller pre-filters to status='new'
    entering in the last 24h); this function keeps score ≥ 7, sorts by
    score desc, caps at ``top_n``, and sends. Sends NOTHING when the
    filtered list is empty — an empty digest is noise. Returns True only
    when an email actually went out.

    ``subject_prefix`` exists for test sends ("[test] " per the session
    rules); production callers leave it empty.
    """
    top = select_morning_digest(jobs, top_n)
    if not top:
        print("[notifier] morning digest: no jobs over the bar — not sending")
        return False

    subject, html_body = _render_morning_digest(top)
    subject = f"{subject_prefix}{subject}"

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print(
            f"[notifier] RESEND_API_KEY not set; would send morning digest "
            f"({len(top)} jobs)"
        )
        return False
    resp = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_ADDR,
            "to": [TO_ADDR],
            "subject": subject,
            "html": html_body,
        },
        timeout=20,
    )
    if resp.status_code >= 300:
        print(f"[notifier] resend failed {resp.status_code}: {resp.text}")
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
#  Tailor / submit — Supabase notifications table (was jobpipe/tailor/notify.py)
# ══════════════════════════════════════════════════════════════════════════

def cockpit_url(job_id: Union[str, int]) -> str:
    """Build a deep link into the dashboard's review cockpit."""
    return f"{PORTFOLIO_BASE_URL}/dashboard/review/{job_id}"


# Lazy module-level Supabase client. Defers connection to first use so
# this module is importable in tests / CI without secrets.
_client = None


def _get_client():
    global _client
    if _client is None:
        # Lazy SDK import — keeps `import jobpipe.notify` cheap.
        # Service-role per the Session E key contract (see README) —
        # SUPABASE_SERVICE_ROLE_KEY falls back to SUPABASE_KEY in config.
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _client


def create_notification(notification_type: str, job: dict, message: str = "") -> bool:
    """Write a row to the ``notifications`` table for the dashboard.

    ``notification_type`` is one of ``"ready_for_review"`` /
    ``"awaiting_human_submit"`` / ``"applied"`` / ``"failed"``. These
    string values are part of the cockpit's contract — they are NOT
    the function-symbol names (the M-8 spec uses ``ready_for_review``
    in the data layer; PR-8 only renamed the Python symbols).
    """
    try:
        client = _get_client()
        client.table("notifications").insert({
            "type": notification_type,
            "job_id": job.get("id"),
            "title": f"{job.get('company', 'Unknown')} — {job.get('title', 'Unknown')}",
            "message": message,
            "read": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info(f"Notification created: {notification_type} for {job.get('company')}")
        return True
    except Exception as e:
        # Don't let notification failures break the pipeline. If the
        # notifications table doesn't exist yet, just log it.
        logger.warning(f"Could not write notification (table may not exist yet): {e}")
        return False


def send_awaiting_review(job: dict) -> bool:
    """Notify that a job application is ready for human review (M-8).

    PR-8 rename: was ``notify_ready_for_review`` pre-PR-8. The
    notification's ``type`` field stays ``"ready_for_review"`` because
    that string is part of the dashboard contract.

    Body now includes score, tier, archetype, and legitimacy alongside
    the cockpit deep link so the dashboard panel renders enough context
    to triage at a glance.
    """
    parts = [
        f"Score: {job.get('score', '?')}/10",
        f"Tier: {job.get('tier', '?')}",
    ]
    if job.get("archetype"):
        parts.append(f"Archetype: {job['archetype']}")
    if job.get("legitimacy"):
        parts.append(f"Legitimacy: {job['legitimacy']}")
    header = " | ".join(parts)

    reasoning = (job.get("reasoning") or "").strip()
    body_lines = [header]
    if reasoning:
        body_lines.append(reasoning)
    body_lines.append(f"Cockpit: {cockpit_url(job.get('id'))}")
    return create_notification(
        "ready_for_review", job, "\n".join(body_lines)
    )


def send_awaiting_submit(job: dict, screenshot_path: str = None) -> bool:
    """Notify that the form has been pre-filled and is awaiting the human's
    review and Submit click in the visible browser (M-5/M-8).

    PR-8 rename: was ``notify_awaiting_submit`` pre-PR-8. The
    notification's ``type`` field stays ``"awaiting_human_submit"`` —
    matches the ``jobs.status`` CHECK enum value the cockpit reads.

    Subject prefix uses [ACTION] so the dashboard notification panel
    surfaces this in the hot-path stack. Body includes the cockpit deep
    link and a reference to the post-fill screenshot the cockpit
    renders inline.
    """
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    body_lines = [
        f"[ACTION] Form pre-filled for {company} - {title} - review and submit.",
        "Browser is open in your local terminal session. Review what was "
        "typed, fix anything wrong, click Submit yourself, then come back "
        "to the dashboard cockpit and click 'Mark Applied'.",
        f"Cockpit: {cockpit_url(job.get('id'))}",
    ]
    if screenshot_path:
        body_lines.append(f"Pre-fill screenshot: {screenshot_path}")
    return create_notification(
        "awaiting_human_submit", job, "\n".join(body_lines)
    )


def send_applied(job: dict) -> bool:
    """Notify that an application was submitted successfully."""
    return create_notification("applied", job, "Application submitted.")


def send_failed(job: dict, reason: str) -> bool:
    """Notify that an application submission failed."""
    return create_notification("failed", job, f"Reason: {reason}")


# The PR-8 deprecated ``notify_*`` → ``send_*`` aliases lived here until
# Session C verified (by grep across code, scripts, and workflows) that
# no callers remained and removed them. ``send_*`` is the only surface.
