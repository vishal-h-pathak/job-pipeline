"""tailor/normalize.py — ASCII-normalize text before PDF render.

ATS resume parsers intermittently fail on Unicode typography (em-dashes,
en-dashes, smart quotes, ligatures). The LLM picks them up from training
data even when prompted not to. This helper runs as a last pass over any
final text we're about to send through pdflatex or reportlab.

Keep the substitution table conservative — only normalize characters
that ATS parsers are known to choke on. Don't strip math symbols,
non-Latin scripts, or anything else a recruiter might want to see.
"""

from __future__ import annotations

import re

# Substitution table: every key in here is a Unicode codepoint that ATS
# parsers commonly mishandle. Values are the ASCII-safe replacement.
_ATS_REPLACEMENTS = {
    # Dashes
    "\u2013": "-",   # en-dash
    "\u2014": " - ", # em-dash (with surrounding spaces — most natural in prose)
    "\u2212": "-",   # minus sign
    "\u2010": "-",   # hyphen
    "\u2011": "-",   # non-breaking hyphen
    # Smart quotes / apostrophes
    "\u2018": "'",   # left single
    "\u2019": "'",   # right single
    "\u201C": '"',   # left double
    "\u201D": '"',   # right double
    "\u201A": "'",   # single low-9
    "\u201E": '"',   # double low-9
    "\u2032": "'",   # prime
    "\u2033": '"',   # double prime
    # Ellipsis + bullets that confuse some parsers
    "\u2026": "...",
    "\u2022": "*",
    # Non-breaking space → regular space
    "\u00A0": " ",
    # Common ligatures pdflatex can render but plain-text ATS extractors mishandle
    "\uFB00": "ff",
    "\uFB01": "fi",
    "\uFB02": "fl",
    "\uFB03": "ffi",
    "\uFB04": "ffl",
}


_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def normalize_for_ats(text: str) -> str:
    """Replace ATS-unfriendly Unicode with ASCII-safe equivalents.

    Idempotent. Safe to call multiple times. Returns the input unchanged
    if it's already ASCII-only and free of repeated spaces.

    The em-dash → " - " mapping intentionally leaves spaces around the
    hyphen so prose like "I built X — and shipped Y" doesn't collapse
    into "I built X-and shipped Y". A trailing pass collapses any
    resulting runs of spaces back to a single space.
    """
    if not text:
        return text
    if all(ord(c) < 128 for c in text) and "  " not in text:
        return text
    out = text
    for src, dst in _ATS_REPLACEMENTS.items():
        if src in out:
            out = out.replace(src, dst)
    out = _MULTI_SPACE.sub(" ", out)
    return out
