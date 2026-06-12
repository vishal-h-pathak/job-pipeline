"""Tests for jobpipe.tailor.manual.cli.run.

We stub the three downstream collaborators (resolve_url,
upsert_manual_job, process_one_approved_job) so the CLI exercises
only its own branching logic + exit codes + stdout shape.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jobpipe.tailor.manual import (
    ScrapeError,
    ScrapedPosting,
    UnsupportedUrl,
)
from jobpipe.tailor.manual.upsert import CollisionError


def _posting(confidence: str = "high") -> ScrapedPosting:
    return ScrapedPosting(
        url="https://job-boards.greenhouse.io/anthropic/jobs/4123456",
        title="ML Researcher",
        company="Anthropic",
        location="San Francisco, CA",
        description="Body",
        ats_kind="greenhouse",
        confidence=confidence,  # type: ignore[arg-type]
    )


def test_cli_high_confidence_runs_tailor_and_prints_materials_url(capsys):
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               return_value=_posting(confidence="high")), \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job",
               return_value=("abc1234567890def", "approved")), \
         patch("jobpipe.tailor.manual.cli._tailor_one",
               return_value="ready_for_review") as tailor:
        from jobpipe.tailor.manual.cli import run
        code = run(["https://job-boards.greenhouse.io/anthropic/jobs/4123456"])

    assert code == 0
    tailor.assert_called_once_with("abc1234567890def")
    out = capsys.readouterr().out
    assert "job_id=abc1234567890def" in out
    assert "status=ready_for_review" in out
    assert "materials_url=/dashboard/review/abc1234567890def" in out
    assert "review_url=" not in out  # high-confidence path uses materials_url


def test_cli_low_confidence_prints_review_url_and_skips_tailor(capsys):
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               return_value=_posting(confidence="low")), \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job",
               return_value=("def4567890abc123", "discovered")), \
         patch("jobpipe.tailor.manual.cli._tailor_one") as tailor:
        from jobpipe.tailor.manual.cli import run
        code = run(["https://acme.example.com/careers/role"])

    assert code == 0
    tailor.assert_not_called()  # critical: Amendment 1 says no tailor on low
    out = capsys.readouterr().out
    assert "job_id=def4567890abc123" in out
    assert "status=discovered" in out
    assert "review_url=/dashboard/review/def4567890abc123" in out
    assert "materials_url=" not in out


def test_cli_returns_2_on_unsupported_url():
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               side_effect=UnsupportedUrl("not an ATS")):
        from jobpipe.tailor.manual.cli import run
        code = run(["https://twitter.com/some-thread"])
    assert code == 2


def test_cli_returns_3_on_scrape_error():
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               side_effect=ScrapeError("HTTP 500")):
        from jobpipe.tailor.manual.cli import run
        code = run(["https://job-boards.greenhouse.io/foo/jobs/1"])
    assert code == 3


def test_cli_returns_4_on_collision():
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               return_value=_posting(confidence="high")), \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job",
               side_effect=CollisionError("abc", "applied")):
        from jobpipe.tailor.manual.cli import run
        code = run(["https://job-boards.greenhouse.io/foo/jobs/1"])
    assert code == 4


def test_cli_status_flag_short_circuits():
    """--status delegates to pipeline.print_status and never scrapes/upserts."""
    with patch("jobpipe.tailor.manual.cli.resolve_url") as resolve, \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job") as upsert, \
         patch("jobpipe.tailor.pipeline.print_status") as ps:
        from jobpipe.tailor.manual.cli import run
        code = run(["--status"])

    assert code == 0
    ps.assert_called_once()
    resolve.assert_not_called()
    upsert.assert_not_called()


def test_cli_errors_when_url_missing_and_no_status():
    """argparse exits 2 when the positional URL is missing."""
    from jobpipe.tailor.manual.cli import run
    with pytest.raises(SystemExit) as exc:
        run([])
    assert exc.value.code == 2


def test_cli_writes_runs_result_when_run_id_supplied_low_confidence(capsys):
    """Low-confidence path with --run-id should write a result payload."""
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               return_value=_posting(confidence="low")), \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job",
               return_value=("def4567890abc123", "discovered")), \
         patch("jobpipe.tailor.manual.cli._write_run_result") as writer:
        from jobpipe.tailor.manual.cli import run
        code = run([
            "https://acme.example.com/careers/role",
            "--run-id", "11111111-2222-3333-4444-555555555555",
        ])

    assert code == 0
    writer.assert_called_once()
    run_id, payload = writer.call_args.args
    assert run_id == "11111111-2222-3333-4444-555555555555"
    assert payload["job_id"] == "def4567890abc123"
    assert payload["status"] == "discovered"
    assert payload["confidence"] == "low"
    assert payload["review_url"] == "/dashboard/review/def4567890abc123"
    assert payload["materials_url"] is None


def test_cli_writes_runs_result_when_run_id_supplied_high_confidence(capsys):
    """High-confidence path with --run-id should record materials_url."""
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               return_value=_posting(confidence="high")), \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job",
               return_value=("abc1234567890def", "approved")), \
         patch("jobpipe.tailor.manual.cli._tailor_one",
               return_value="ready_for_review"), \
         patch("jobpipe.tailor.manual.cli._write_run_result") as writer:
        from jobpipe.tailor.manual.cli import run
        code = run([
            "https://job-boards.greenhouse.io/anthropic/jobs/4123456",
            "--run-id", "22222222-3333-4444-5555-666666666666",
        ])

    assert code == 0
    writer.assert_called_once()
    _, payload = writer.call_args.args
    assert payload["job_id"] == "abc1234567890def"
    assert payload["status"] == "ready_for_review"
    assert payload["confidence"] == "high"
    assert payload["materials_url"] == "/dashboard/review/abc1234567890def"
    assert payload["review_url"] is None


def test_cli_skips_runs_result_when_no_run_id():
    with patch("jobpipe.tailor.manual.cli.resolve_url",
               return_value=_posting(confidence="low")), \
         patch("jobpipe.tailor.manual.cli.upsert_manual_job",
               return_value=("abc", "discovered")), \
         patch("jobpipe.tailor.manual.cli._write_run_result") as writer:
        from jobpipe.tailor.manual.cli import run
        run(["https://acme.example.com/careers/role"])
    writer.assert_not_called()


def test_write_run_result_swallows_supabase_errors(patch_db_client):
    """If the runs.result column doesn't exist yet (pre-migration-009)
    or the network blips, the CLI must NOT crash — degraded UX only."""
    from jobpipe.tailor.manual.cli import _write_run_result

    class _BoomClient:
        def table(self, _):
            raise RuntimeError("column \"result\" does not exist")

    patch_db_client(_BoomClient())

    # Must not raise
    _write_run_result("some-uuid", {"job_id": "x", "status": "discovered"})
