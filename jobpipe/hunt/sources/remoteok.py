import hashlib
import re

import requests

ENDPOINT = "https://remoteok.com/api"

KEYWORDS = [
    "neuromorphic",
    "neuroscience",
    "spiking",
    "connectomics",
    "machine learning",
    "computer vision",
    "sales engineer",
    "developer relations",
    "solutions engineer",
]

TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def _job_id(link: str, title: str, company: str) -> str:
    return hashlib.sha1(f"remoteok|{link}|{title}|{company}".encode()).hexdigest()[:16]


def _matches(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in KEYWORDS)


def fetch():
    """Yield job dicts from the RemoteOK public API, filtered by keyword."""
    headers = {"User-Agent": "job-hunter/1.0"}
    resp = requests.get(ENDPOINT, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # First element is a legend/metadata object — skip non-job entries.
    for entry in data:
        if not isinstance(entry, dict) or "id" not in entry or "position" not in entry:
            continue
        title = entry.get("position", "")
        company = entry.get("company", "Unknown")
        description = _strip_html(entry.get("description", ""))
        tags = " ".join(entry.get("tags", []) or [])
        haystack = f"{title} {description} {tags}"
        if not _matches(haystack):
            continue
        link = entry.get("url") or entry.get("apply_url") or ""
        location = entry.get("location") or "Remote"
        jid = _job_id(link, title, company)
        yield {
            "id": jid,
            "source": "remoteok",
            "query": "",
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "url": link,
        }
