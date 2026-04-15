import hashlib
import re
import time
from urllib.parse import urlencode

import feedparser

QUERIES = [
    "neuromorphic",
    "computational neuroscience",
    "spiking neural network",
    "connectomics",
    "sales engineer LLM",
    "sales engineer AI",
    "sales engineer neuromorphic",
    "sales engineer brain computer interface",
    "technical sales AI startup",
    "solutions engineer machine learning",
    "developer relations AI",
    "developer advocate machine learning",
]

LOCATIONS = [
    {"l": "Atlanta, GA", "label": "Atlanta, GA"},
    {"l": "Remote", "label": "Remote"},
]

BASE = "https://www.indeed.com/rss"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def _job_id(link: str, title: str, company: str) -> str:
    return hashlib.sha1(f"indeed|{link}|{title}|{company}".encode()).hexdigest()[:16]


def fetch():
    """Yield job dicts from Indeed RSS across the keyword/location matrix."""
    seen_local = set()
    for q in QUERIES:
        for loc in LOCATIONS:
            url = f"{BASE}?{urlencode({'q': q, 'l': loc['l']})}"
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title_raw = entry.get("title", "")
                # Indeed RSS titles are typically "Job Title - Company - Location"
                parts = [p.strip() for p in title_raw.split(" - ")]
                title = parts[0] if parts else title_raw
                company = parts[1] if len(parts) > 1 else "Unknown"
                location = parts[2] if len(parts) > 2 else loc["label"]
                link = entry.get("link", "")
                description = _strip_html(entry.get("summary", ""))
                jid = _job_id(link, title, company)
                if jid in seen_local:
                    continue
                seen_local.add(jid)
                yield {
                    "id": jid,
                    "source": "indeed",
                    "query": q,
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": description,
                    "url": link,
                }
            time.sleep(1)
