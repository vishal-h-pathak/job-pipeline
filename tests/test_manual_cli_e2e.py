"""End-to-end smoke for jobpipe-tailor-one against the three structured-ATS fixtures.

Wires the whole pipeline:
    cli.run
      → resolve_url
        → resolve_application_url (stubbed pass-through)
        → detect_ats
        → per-ATS fetcher  (mocked httpx, fixture-driven)
      → upsert_manual_job   (jobpipe.db.client stubbed in-memory)
      → _tailor_one         (stubbed — the real one needs LaTeX + LLM)

Verifies the checkpoint requirement from Amendment 2: the CLI exits 0
against each fixture and prints the expected stdout shape, end-to-end,
without the per-module mocking the unit tests use.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "manual"


def _mock_httpx_routes(routes: dict):
    def fake_get(url, *a, **kw):
        for substr, payload in routes.items():
            if substr in url:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = payload
                resp.text = json.dumps(payload)
                resp.raise_for_status.return_value = None
                return resp
        raise AssertionError(f"e2e: unexpected URL: {url}")

    client = MagicMock()
    client.get.side_effect = fake_get
    ctx = MagicMock()
    ctx.__enter__.return_value = client
    ctx.__exit__.return_value = False
    return ctx


class _StubQuery:
    def __init__(self):
        self.inserted: dict | None = None
        self.updated: dict | None = None

    def select(self, _cols):
        return self

    def eq(self, _col, _val):
        return self

    def execute(self):
        # Empty existing-rows on select; benign on insert/update/etc.
        return MagicMock(data=[])

    def insert(self, payload):
        self.inserted = payload
        return self

    def update(self, payload):
        self.updated = payload
        return self


class _StubClient:
    def __init__(self):
        self.q = _StubQuery()

    def table(self, _name):
        return self.q


@pytest.fixture(autouse=True)
def stub_db(monkeypatch):
    """Replace jobpipe.db.client with an in-memory stub for the whole test."""
    import jobpipe.db
    stub = _StubClient()
    monkeypatch.setattr(jobpipe.db, "client", stub, raising=False)
    return stub


@pytest.fixture(autouse=True)
def stub_tailor(monkeypatch):
    """process_one_approved_job is the LaTeX + LLM stack — never run in tests.

    We stub the CLI's _tailor_one helper to return a representative final
    status so the e2e assertion still verifies the stdout shape.
    """
    monkeypatch.setattr(
        "jobpipe.tailor.manual.cli._tailor_one",
        lambda job_id: "ready_for_review",
    )


@pytest.fixture(autouse=True)
def stub_resolver(monkeypatch):
    """resolve_application_url does live httpx — pass URL through unchanged."""
    monkeypatch.setattr(
        "jobpipe.tailor.manual.resolve.resolve_application_url",
        lambda u, **kw: {
            "resolved": u, "is_ats": True, "trail": [u], "notes": ""
        },
    )


# ── Per-fixture e2e tests ────────────────────────────────────────────────

def test_e2e_greenhouse(capsys):
    routes = {
        "/boards/anthropic/jobs/4123456":
            json.loads((FIXTURES / "greenhouse_job.json").read_text()),
        "/boards/anthropic":
            json.loads((FIXTURES / "greenhouse_board.json").read_text()),
    }
    with patch("httpx.Client", return_value=_mock_httpx_routes(routes)):
        from jobpipe.tailor.manual.cli import run
        code = run(["https://job-boards.greenhouse.io/anthropic/jobs/4123456"])

    assert code == 0
    out = capsys.readouterr().out
    assert "status=ready_for_review" in out
    assert "materials_url=/dashboard/review/" in out


def test_e2e_lever(capsys):
    routes = {
        "v0/postings/anthropic":
            json.loads((FIXTURES / "lever_posting.json").read_text()),
    }
    with patch("httpx.Client", return_value=_mock_httpx_routes(routes)):
        from jobpipe.tailor.manual.cli import run
        code = run([
            "https://jobs.lever.co/anthropic/"
            "abc12345-def6-7890-abcd-ef1234567890",
        ])

    assert code == 0
    out = capsys.readouterr().out
    assert "status=ready_for_review" in out
    assert "materials_url=/dashboard/review/" in out


def test_e2e_ashby(capsys):
    routes = {
        "job-board/eonsystems":
            json.loads((FIXTURES / "ashby_board.json").read_text()),
    }
    with patch("httpx.Client", return_value=_mock_httpx_routes(routes)):
        from jobpipe.tailor.manual.cli import run
        code = run([
            "https://jobs.ashbyhq.com/eonsystems/"
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        ])

    assert code == 0
    out = capsys.readouterr().out
    assert "status=ready_for_review" in out
    assert "materials_url=/dashboard/review/" in out


def test_e2e_generic_low_confidence_routes_to_review(capsys):
    """Bonus coverage: a non-Greenhouse/Lever/Ashby URL routes through
    the generic Playwright fallback, lands at status='discovered', and
    prints the review URL — never invokes _tailor_one (Amendment 1)."""
    html = (FIXTURES / "generic_jobposting.html").read_text()
    # Patch the binding inside resolve.py (where it was imported), not
    # the source module — `from .scrape_generic import fetch_generic_posting`
    # snapshotted the name at import time.
    from jobpipe.tailor.manual.scrape_generic import parse_jobposting_html
    with patch(
        "jobpipe.tailor.manual.resolve.fetch_generic_posting",
        side_effect=lambda url, **kw: parse_jobposting_html(html, url),
    ), patch("jobpipe.tailor.manual.cli._tailor_one") as tailor:

        from jobpipe.tailor.manual.cli import run
        code = run(["https://acme.example.com/careers/ml-research"])

    assert code == 0
    tailor.assert_not_called()
    out = capsys.readouterr().out
    assert "status=discovered" in out
    assert "review_url=/dashboard/review/" in out
