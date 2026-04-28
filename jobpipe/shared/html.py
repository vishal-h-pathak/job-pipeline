"""jobpipe.shared.html — HTML scrubbing helpers shared across subsystems.

Hunt sources, the description enricher, and (eventually) tailor/submit modules
all need to peel HTML out of fetched job descriptions. Three flavors used to
live duplicated across ~9 files; they collapse into the two helpers below.

- ``strip_tags`` — bare tag removal. The most common use: ATS APIs already
  hand us text wrapped in `<p>` / `<li>` and a quick `<...>` strip is
  enough for the LLM scorer to read it.
- ``clean_html_to_text`` — full normalize: HTML-entity unescape (`&amp;`
  → `&`), tags replaced with spaces (so adjacent words don't fuse), and
  whitespace collapsed. Use when the source mixes inline tags inside
  paragraphs (HN `<p>` separators, 80kh job-description blobs).

Both are pure functions and tolerate ``None`` / empty input.
"""

from __future__ import annotations

import re
from html import unescape

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_tags(text: str) -> str:
    """Remove HTML tags and surrounding whitespace.

    Equivalent to the inline ``TAG_RE.sub("", text or "").strip()`` that
    used to live in ashby / greenhouse / lever / remoteok / workday / indeed.
    """
    return _TAG_RE.sub("", text or "").strip()


def clean_html_to_text(text: str) -> str:
    """Unescape entities, replace tags with spaces, collapse whitespace.

    Use this when the source HTML has paragraph / list-item tags adjacent
    to running text — naive tag-removal would fuse the words. Used by
    hn_whoshiring, eighty_thousand_hours, and the description enricher.
    """
    text = unescape(text or "")
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()
