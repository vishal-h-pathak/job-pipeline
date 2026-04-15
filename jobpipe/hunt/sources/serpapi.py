import hashlib
import os
import time

import requests

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
    {"location": "Atlanta, Georgia, United States", "label": "Atlanta, GA"},
    {"location": "United States", "label": "Remote", "remote": True},
]

ENDPOINT = "https://serpapi.com/search.json"


def _job_id(link: str, title: str, company: str) -> str:
    return hashlib.sha1(f"serpapi|{link}|{title}|{company}".encode()).hexdigest()[:16]


def fetch():
    """Yield job dicts from SerpAPI's Google Jobs endpoint."""
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        return
    seen_local = set()
    for q in QUERIES:
        for loc in LOCATIONS:
            query = f"{q} remote" if loc.get("remote") else q
            params = {
                "engine": "google_jobs",
                "q": query,
                "location": loc["location"],
                "api_key": api_key,
            }
            try:
                resp = requests.get(ENDPOINT, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                time.sleep(1)
                continue
            for job in data.get("jobs_results", []) or []:
                title = job.get("title", "")
                company = job.get("company_name", "Unknown")
                location = job.get("location", loc["label"])
                description = job.get("description", "")
                link = ""
                for opt in job.get("apply_options", []) or []:
                    if opt.get("link"):
                        link = opt["link"]
                        break
                if not link:
                    link = job.get("share_link") or job.get("job_id", "")
                jid = _job_id(link, title, company)
                if jid in seen_local:
                    continue
                seen_local.add(jid)
                yield {
                    "id": jid,
                    "source": "serpapi",
                    "query": q,
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": description,
                    "url": link,
                }
            time.sleep(1)
