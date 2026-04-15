import sys
import traceback

from dotenv import load_dotenv

load_dotenv()

from sources import indeed, remoteok, serpapi, wellfound  # noqa: E402
from scorer import score_job, should_notify  # noqa: E402
from notifier import send_digest  # noqa: E402
from utils.validator import validate_url  # noqa: E402
from db import get_seen_ids, upsert_job  # noqa: E402


def iter_all_jobs():
    for src in (indeed, wellfound, serpapi, remoteok):
        try:
            yield from src.fetch()
        except Exception as e:
            print(f"[{src.__name__}] error: {e}", file=sys.stderr)
            traceback.print_exc()


def run() -> None:
    seen = get_seen_ids()
    new_count = 0
    to_notify: list[dict] = []
    for job in iter_all_jobs():
        if job["id"] in seen:
            continue
        new_count += 1
        try:
            result = score_job(
                title=job["title"],
                company=job["company"],
                description=job["description"],
                location=job["location"],
            )
        except Exception as e:
            print(f"[scorer] error on {job['title']!r}: {e}", file=sys.stderr)
            continue

        if should_notify(result):
            if validate_url(job["url"]):
                to_notify.append({"job": job, "score": result})
            else:
                result = {**result, "skipped_reason": "dead_link"}
                print(f"[validator] dead link, skipping: {job['url']}")

        try:
            upsert_job(job, result)
            seen.add(job["id"])
        except Exception as e:
            print(f"[db] upsert error for {job['id']}: {e}", file=sys.stderr)

    if to_notify:
        send_digest(to_notify)

    print(f"done. new jobs: {new_count}, notified: {len(to_notify)}")


if __name__ == "__main__":
    run()
