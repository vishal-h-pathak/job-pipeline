"""tests/test_morning_digest.py — Session I: jobpipe.notify.send_morning_digest.

Policy pins: score ≥ 7 bar, score-desc ordering, top-N cap, empty →
no send at all, deep links to /dashboard/review/{job_id}, tier pill
renders the half tier, and no HTTP leaves the building without
RESEND_API_KEY.
"""

from __future__ import annotations

import pytest

from jobpipe import notify


def _job(i: int, score, tier=1, reasoning="Strong neuromorphic fit."):
    return {
        "id": f"job-{i}",
        "title": f"Role {i}",
        "company": f"Co{i}",
        "tier": tier,
        "score": score,
        "reasoning": reasoning,
        "status": "new",
    }


@pytest.fixture
def no_http(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("requests.post must not be called")

    monkeypatch.setattr(notify.requests, "post", _boom)


@pytest.fixture
def capture_http(monkeypatch):
    sent: list[dict] = []

    class _Resp:
        status_code = 200
        text = "ok"

    def _post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "json": json})
        return _Resp()

    monkeypatch.setattr(notify.requests, "post", _post)
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    return sent


def test_selection_applies_bar_sort_and_cap():
    jobs = [_job(i, score=i) for i in range(5, 12)]  # scores 5..11
    top = notify.select_morning_digest(jobs)
    assert [j["score"] for j in top] == [11, 10, 9, 8, 7]
    # String scores coerce; junk scores drop.
    assert notify.select_morning_digest([_job(1, "8"), _job(2, "n/a")]) == [
        _job(1, "8")
    ]


def test_empty_and_sub_bar_send_nothing(no_http, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    assert notify.send_morning_digest([]) is False
    assert notify.send_morning_digest([_job(1, score=6)]) is False


def test_missing_api_key_sends_nothing(no_http, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert notify.send_morning_digest([_job(1, score=9)]) is False


def test_send_renders_subject_links_and_pills(capture_http):
    jobs = [_job(1, score=9, tier="1.5"), _job(2, score=8, tier=1)]
    assert notify.send_morning_digest(jobs) is True

    assert len(capture_http) == 1
    payload = capture_http[0]["json"]
    assert payload["subject"] == "hunt digest — 2 worth a look"
    html_body = payload["html"]
    assert "/dashboard/review/job-1" in html_body
    assert "/dashboard/review/job-2" in html_body
    assert "Tier 1.5" in html_body
    assert "Strong neuromorphic fit." in html_body
    assert "9/10" in html_body


def test_subject_prefix_for_test_sends(capture_http):
    notify.send_morning_digest([_job(1, score=9)], subject_prefix="[test] ")
    assert capture_http[0]["json"]["subject"].startswith("[test] hunt digest")


def test_one_line_collapses_whitespace_and_truncates():
    assert notify._one_line("a\n b\t c") == "a b c"
    long = "word " * 100
    out = notify._one_line(long, limit=50)
    assert len(out) <= 54 and out.endswith("...")
