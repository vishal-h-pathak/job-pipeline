import html
import os

import requests

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
    """entries: list of {"job": job_dict, "score": score_dict}."""
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
