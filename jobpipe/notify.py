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
    - The pre-PR-8 ``notify_*`` names remain as deprecated aliases with
      a once-per-process ``logger.warning`` so call sites can migrate
      incrementally. Future PR sweeps the aliases.
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
    SUPABASE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger("jobpipe.notify")


# ══════════════════════════════════════════════════════════════════════════
#  Hunt — Resend HTML email digest (was jobpipe/hunt/notifier.py)
# ══════════════════════════════════════════════════════════════════════════

RESEND_URL = "https://api.resend.com/emails"
FROM_ADDR = os.environ.get("NOTIFY_FROM", "Job Agent <jobs@vishal.pa.thak.io>")
TO_ADDR = os.environ.get("NOTIFY_TO", "vishal@pa.thak.io")


def _tier_key(tier) -> int:
    if isinstance(tier, int):
        return tier
    if isinstance(tier, str) and tier.isdigit():
        return int(tier)
    return 99


def _render_job(job: dict, score: dict) -> str:
    return f"""
    <div style="border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px;
                margin-bottom: 14px;">
      <h3 style="margin: 0 0 4px 0;">{html.escape(job['title'])}</h3>
      <div style="color: #555; margin-bottom: 8px;">
        {html.escape(job['company'])} · {html.escape(job['location'])}
        &nbsp;·&nbsp; <strong>{score.get('score')}/10</strong>
      </div>
      <p style="line-height: 1.5; margin: 8px 0;">
        {html.escape(score.get('reasoning', ''))}
      </p>
      <p style="margin: 8px 0 0 0;">
        <a href="{html.escape(job['url'])}"
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
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
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


# ══════════════════════════════════════════════════════════════════════════
#  Deprecated aliases (PR-8): notify_* names from pre-PR-8 callers.
#  Each alias logs once per process, then forwards. Sweep in a follow-up.
# ══════════════════════════════════════════════════════════════════════════

_warned_aliases: set[str] = set()


def _warn_alias_once(old: str, new: str) -> None:
    if old in _warned_aliases:
        return
    _warned_aliases.add(old)
    logger.warning(
        "%s() is deprecated (PR-8); use %s() instead. "
        "This warning fires once per process.",
        old, new,
    )


def notify_ready_for_review(job: dict) -> bool:
    """DEPRECATED — use :func:`send_awaiting_review`. Forwards unchanged."""
    _warn_alias_once("notify_ready_for_review", "send_awaiting_review")
    return send_awaiting_review(job)


def notify_awaiting_submit(job: dict, screenshot_path: str = None) -> bool:
    """DEPRECATED — use :func:`send_awaiting_submit`. Forwards unchanged."""
    _warn_alias_once("notify_awaiting_submit", "send_awaiting_submit")
    return send_awaiting_submit(job, screenshot_path)


def notify_applied(job: dict) -> bool:
    """DEPRECATED — use :func:`send_applied`. Forwards unchanged."""
    _warn_alias_once("notify_applied", "send_applied")
    return send_applied(job)


def notify_failed(job: dict, reason: str) -> bool:
    """DEPRECATED — use :func:`send_failed`. Forwards unchanged."""
    _warn_alias_once("notify_failed", "send_failed")
    return send_failed(job, reason)
