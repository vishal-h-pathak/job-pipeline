"""scripts/send_morning_digest.py — query + send the morning hunt digest.

Session I: hunt.yml's cron path runs this right after the hunt
completes (continue-on-error — a digest failure must never fail the
hunt). Pulls jobs that entered 'new' in the last 24h, hands them to
``jobpipe.notify.send_morning_digest`` which applies the score-≥7 bar,
sorts, caps at top 5, and sends nothing when the list is empty.

Usage:
    python scripts/send_morning_digest.py             # query + send
    python scripts/send_morning_digest.py --dry-run   # render to stdout, no email
    python scripts/send_morning_digest.py --hours 48 --top-n 3
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from jobpipe import notify  # noqa: E402


def fetch_new_jobs(hours: int) -> list[dict]:
    from jobpipe.db import client

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        client.table("jobs")
        .select("id,title,company,tier,score,reasoning,created_at,status")
        .eq("status", "new")
        .gte("created_at", since)
        .order("score", desc=True)
        .limit(50)
        .execute()
    )
    return res.data or []


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the morning hunt digest")
    parser.add_argument("--hours", type=int, default=24,
                        help="Lookback window for jobs entering 'new'. Default: %(default)s.")
    parser.add_argument("--top-n", type=int, default=notify.MORNING_DIGEST_TOP_N,
                        help="Max jobs in the digest. Default: %(default)s.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render subject + HTML to stdout instead of sending.")
    args = parser.parse_args()

    jobs = fetch_new_jobs(args.hours)
    print(f"[morning-digest] {len(jobs)} jobs entered 'new' in the last {args.hours}h")

    if args.dry_run:
        top = notify.select_morning_digest(jobs, args.top_n)
        if not top:
            print("[morning-digest] dry run: nothing over the bar — would not send")
            return 0
        subject, body = notify._render_morning_digest(top)
        print(f"[morning-digest] dry run subject: {subject}")
        print(body)
        return 0

    sent = notify.send_morning_digest(jobs, top_n=args.top_n)
    print(f"[morning-digest] sent={sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
