"""
Run Phase 1 tailoring for Beacon Biosignals (Neuroscientist, External Scientific Programs).

Supabase is unreachable from the sandbox, so this script calls the tailoring
functions directly with the job dict embedded here (extracted from the
Greenhouse posting on 2026-04-19).

Outputs land in job-applicant/output/:
  - resume_tailoring_Beacon_Biosignals_<ts>.json   (Phase-1 tailoring plan)
  - resume_Beacon_Biosignals_<ts>.tex              (LaTeX source)
  - resume_Beacon_Biosignals_<ts>.pdf              (compiled PDF)
  - cover_letter_Beacon_Biosignals_<ts>.txt        (plain-text cover letter)
"""

import json
import os
import sys
from pathlib import Path

# Proxy CA handling for sandbox
os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

# Make imports work regardless of cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from tailor.resume import tailor_resume
from tailor.cover_letter import generate_cover_letter
from tailor.latex_resume import generate_tailored_latex


JOB = {
    "id": "beacon-neuroscientist-esp-2026",
    "title": "Neuroscientist, External Scientific Programs",
    "company": "Beacon Biosignals",
    "location": "Boston, MA - Remote (US preferred)",
    "url": "https://boards.greenhouse.io/beaconbiosignals/jobs/4110972009",
    "application_url": "https://boards.greenhouse.io/beaconbiosignals/jobs/4110972009",
    "description": """Beacon Biosignals is on a mission to revolutionize precision medicine for the brain. We are the leading at-home EEG platform supporting clinical development of novel therapeutics for neurological, psychiatric, and sleep disorders. Our FDA 510(k)-cleared Waveband EEG headband and AI algorithms enable quantitative biomarker discovery and implementation. Beacon's Clinico-EEG database contains EEG data from nearly 100,000 patients, and our cloud-native analytics platform powers large-scale RWD/RWE retrospective and predictive studies.

Beacon Biosignals is seeking an experienced neuroscientist/data scientist to serve as a scientific partner to Beacon's Life Science customers, working directly with pharmaceutical and biotechnology teams to analyze clinical trial data and extract meaningful insights using Beacon's devices and AI/ML models. In this customer-facing role, you'll be embedded in active client engagements, helping translate complex neural data into scientifically rigorous, decision-relevant results that inform drug development programs across neurological, psychiatric, and sleep disorders.

As a member of Beacon's Data Science Group, you will design and execute analyses on real-world and clinical trial EEG data, applying statistical, computational, and neuroscientific expertise. You will work closely with customer scientists, clinicians, and program leaders—leading scientific discussions, presenting results, and iterating collaboratively as studies evolve. You will also act as a critical bridge between customers and Beacon's other internal teams.

What success looks like:
- Lead and support the scientific and technical aspects of multiple concurrent studies, including timeline estimation, task delegation, timely execution, and stakeholder communication
- Engage with external stakeholders to align their needs with internal capabilities and capacities to ensure analyses bring maximal value while remaining operationally feasible
- Deliver clear and impactful presentations and data visualizations to varied audiences, both internal and external
- Leverage Beacon's products and your own subject matter expertise to inform analysis planning and execution
- Write and review reusable, documented, tested code that produces polished, high quality analytical results for our partners and powers our core computational pipelines
- Contribute to statistical analysis plans for clinical trials in collaboration with internal subject matter experts and external stakeholders
- Co-author scientific reports whose impact pushes the field past contemporary limitations
- Dig into large, messy, unfamiliar datasets, document their idiosyncrasies and provenance, and harmonize them with Beacon's Datastore

What you will bring:
- Multiple years using expertise in neuroscience and statistics to analyze data and answer scientific questions for stakeholders
- Substantial experience presenting analyses to external audiences of varied backgrounds
- Thrive in a fast-paced, highly customer-facing environment
- Excellent written and verbal communication and listening skills
- Experience writing and reviewing code in a shared, version-controlled codebase
- Familiarity with statistical modeling, including interpretation and diagnostics
- Familiar with and/or excited to work with Julia, AWS, Superset, Pandoc, SQL, GraphQL
- Exceptional attention to detail
- Comfortable in a highly asynchronous hybrid work environment

Salary range: $135,000 – $155,000.""",
    "score": 8,
    "tier": 1,
    "reasoning": "Neuroscience + EEG + data science role. Vishal has strong computational neuroscience and neural simulation background, though his EEG experience is neuromorphic/simulation-adjacent, not clinical trial EEG. The customer-facing scientific partner angle is a stretch — he has sales engineering inclination but no direct pharma client work. Match strengths: neuroscience depth, statistical/ML coding, Python, scientific writing, communication. Match gaps: clinical trial / pharma experience, Julia, GraphQL, direct patient-data analysis.",
}


def main():
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)

    print(">> Phase 1: resume tailoring (Claude JSON)…")
    tailoring = tailor_resume(JOB)
    print(f"   tailoring output path: {tailoring.get('output_path')}")
    print(f"   emphasis areas: {tailoring.get('emphasis_areas')}")

    print("\n>> Phase 2: LaTeX resume → PDF…")
    latex_result = generate_tailored_latex(JOB, tailoring)
    print(f"   tex: {latex_result.get('tex_path')}")
    print(f"   pdf: {latex_result.get('pdf_path')}")
    print(f"   compile_success: {latex_result.get('compile_success')}")
    if not latex_result.get("compile_success"):
        print(f"   compile log tail:\n{latex_result.get('compile_log', '')[-800:]}")

    print("\n>> Phase 3: cover letter…")
    cover = generate_cover_letter(JOB, resume_tailoring=tailoring)
    print(f"   cover letter path: {cover.get('output_path')}")

    # Write a combined summary so the next step can find everything easily
    summary = {
        "job_id": JOB["id"],
        "title": JOB["title"],
        "company": JOB["company"],
        "application_url": JOB["application_url"],
        "resume_tailoring_path": tailoring.get("output_path"),
        "resume_tex_path": latex_result.get("tex_path"),
        "resume_pdf_path": latex_result.get("pdf_path"),
        "cover_letter_path": cover.get("output_path"),
        "cover_letter_text": cover.get("cover_letter"),
    }
    summary_path = out_dir / f"beacon_application_bundle.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n>> bundle written: {summary_path}")


if __name__ == "__main__":
    main()
