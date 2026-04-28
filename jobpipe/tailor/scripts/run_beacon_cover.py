"""Run cover-letter generation only for Beacon Biosignals, reusing existing resume tailoring."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from tailor.cover_letter import generate_cover_letter

# Reuse the existing tailoring JSON
tailoring_path = ROOT / "output" / "resume_tailoring_Beacon_Biosignals_20260419_182000.json"
tailoring = json.loads(tailoring_path.read_text())

JOB = {
    "id": "beacon-neuroscientist-esp-2026",
    "title": "Neuroscientist, External Scientific Programs",
    "company": "Beacon Biosignals",
    "location": "Boston, MA - Remote (US preferred)",
    "url": "https://boards.greenhouse.io/beaconbiosignals/jobs/4110972009",
    "description": """Beacon Biosignals is on a mission to revolutionize precision medicine for the brain, developing the leading at-home EEG platform supporting clinical development of novel therapeutics for neurological, psychiatric, and sleep disorders. The Waveband EEG headband is FDA 510(k)-cleared; their Clinico-EEG database contains EEG data from nearly 100,000 patients; their cloud-native analytics platform powers large-scale RWD/RWE retrospective and predictive studies.

This role: experienced neuroscientist/data scientist serving as scientific partner to Beacon's Life Science customers — pharma and biotech teams. Embedded in active client engagements. Design and execute analyses on real-world and clinical trial EEG data, applying statistical, computational, and neuroscientific expertise. Lead scientific discussions, present results, contribute to statistical analysis plans for clinical trials. Act as a bridge between customers and Beacon's internal teams to inform product and model development.

Looking for: multiple years of neuroscience + statistics analysis for stakeholders; presenting to external audiences; fast-paced customer-facing environment; version-controlled shared codebase; statistical modeling. Familiarity with Julia, AWS, Superset, Pandoc, SQL, GraphQL. Async hybrid work. Salary $135k-$155k.""",
    "score": 8,
    "tier": 1,
}

cover = generate_cover_letter(JOB, resume_tailoring=tailoring)
print("cover letter path:", cover.get("output_path"))
print("\n--- COVER LETTER ---")
print(cover.get("cover_letter"))
