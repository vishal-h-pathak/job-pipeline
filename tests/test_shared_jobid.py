"""PR-1 regression test for jobpipe.shared.jobid.make_job_id.

Pins golden hashes so any future change to make_job_id (or its helpers
canonical_url / _normalise_text) trips the test before reaching production.
make_job_id is a god-node in cross-source dedup (15 callers per the graphify
report); silent drift would re-double-count jobs and inflate the queue.
"""

from jobpipe.shared.jobid import canonical_url, make_job_id


GREENHOUSE_GOLDEN = "d5456c62919d9c3a"
EXAMPLE_GOLDEN = "025c622cda7392f6"
EMPTY_URL_GOLDEN = "58ef9d5633aa5aaf"


def test_make_job_id_golden_canonical_case() -> None:
    assert make_job_id(
        "https://boards.greenhouse.io/foo/jobs/123",
        "Senior Engineer",
        "Foo Inc",
    ) == GREENHOUSE_GOLDEN


def test_make_job_id_host_alias_normalized() -> None:
    """boards.greenhouse.io and job-boards.greenhouse.io collapse to the same id."""
    a = make_job_id(
        "https://boards.greenhouse.io/foo/jobs/123", "Senior Engineer", "Foo Inc"
    )
    b = make_job_id(
        "https://job-boards.greenhouse.io/foo/jobs/123", "Senior Engineer", "Foo Inc"
    )
    assert a == b == GREENHOUSE_GOLDEN


def test_make_job_id_strips_query_trailing_slash_and_case() -> None:
    """Tracking params, trailing slashes, and case differences do not affect the id."""
    base = make_job_id(
        "https://boards.greenhouse.io/foo/jobs/123", "Senior Engineer", "Foo Inc"
    )
    with_query = make_job_id(
        "https://boards.greenhouse.io/foo/jobs/123?utm_source=x",
        "Senior Engineer",
        "Foo Inc",
    )
    upper_slash = make_job_id(
        "https://boards.greenhouse.io/foo/jobs/123/",
        "SENIOR ENGINEER",
        "FOO INC",
    )
    assert base == with_query == upper_slash == GREENHOUSE_GOLDEN


def test_make_job_id_strips_remote_noise() -> None:
    """'(Remote)' in titles and trailing punctuation in company drop out of the hash."""
    assert make_job_id(
        "https://example.com/jobs/abc",
        "ML Engineer (Remote)",
        "Acme Co.",
    ) == EXAMPLE_GOLDEN


def test_make_job_id_handles_missing_url() -> None:
    """Missing URL still yields a deterministic id from (company, title)."""
    assert make_job_id("", "Just A Title", "Just A Company") == EMPTY_URL_GOLDEN


def test_make_job_id_is_deterministic() -> None:
    a = make_job_id("https://x.com/jobs/1", "Eng", "X")
    b = make_job_id("https://x.com/jobs/1", "Eng", "X")
    assert a == b


def test_make_job_id_length_and_alphabet() -> None:
    out = make_job_id("https://x.com/jobs/1", "Eng", "X")
    assert len(out) == 16
    assert all(c in "0123456789abcdef" for c in out)


def test_canonical_url_drops_fragment_and_query() -> None:
    assert (
        canonical_url("https://example.com/jobs/1?x=2#frag")
        == "https://example.com/jobs/1"
    )
