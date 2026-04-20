"""
tailor/latex_resume.py — Generate tailored LaTeX resumes compiled to PDF.

Takes the output of tailor_resume() and produces a LaTeX document matching
Vishal's existing resume style (Comp Neuroscience variant), then compiles to PDF.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CANDIDATE_PROFILE_PATH

logger = logging.getLogger("tailor.latex_resume")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Base resume data (source of truth — never fabricate beyond this) ──────

BASE_RESUME = {
    "name": "Vishal Pathak",
    "email": "vishalp@thak.io",
    "location": "Atlanta, GA",
    "linkedin": "linkedin.com/in/vishalhpathak",
    "website": "vishal.pa.thak.io",
    "education": {
        "school": "Florida Institute of Technology",
        "degree": "B.S. Electrical Engineering, cum laude",
        "period": "2019--2021",
    },
    "skills": {
        "Neuromorphic & Simulation": "Intel LavaSDK, NxSDK, Brian2, MuJoCo, Gymnasium API, FlyGym, VHDL, RTL design, AFSIM surrogate modeling",
        "Programming & ML": "Python, C/C++, PyTorch, TensorFlow, NumPy, Matplotlib, scikit-learn, PyQt6",
        "Systems & Hardware": "FPGA development, embedded systems (STM32), PCB design (EAGLE/Altium), serial protocols (RS-232/RS-485), ruggedized sensor deployment, HPC clusters",
        "Tools & Platforms": "Git, CI/CD (Jacamar-CI), pytest, Docker, Linux, MATLAB, LabVIEW",
    },
    "experience": [
        {
            "org": "Georgia Tech Research Institute",
            "title": "Algorithms \\& Analysis Engineer",
            "location": "Atlanta, GA",
            "period": "August 2021 -- Present",
            "projects": [
                {
                    "name": "SPARSE: Spiking Processing for Autonomous RF \\& Sensor Engineering",
                    "period": "Aug 2021 -- Jul 2024",
                    "bullets": [
                        "Developed VHDL models of CUBA and LIF neurons matching Intel's LavaSDK behavior, enabling seamless deployment of spiking neural networks from simulation to FPGA hardware",
                        "Deployed and benchmarked custom spiking networks on Intel's Kapoho Bay neuromorphic platform, evaluating power consumption and inference performance for edge applications",
                        "Contributed to DNN$\\to$SNN conversion pipeline using backpropagation in the spiking regime for overhead imagery and radar signal processing applications",
                        "Trained deep learning models on GTRI's ICEHAMMER HPC cluster using PyTorch and TensorFlow frameworks",
                    ],
                },
                {
                    # NOTE: Spynel band below is assumed MWIR based on HGH's Spynel-S/X
                    # line (the flagship MWIR panoramic thermal cameras); Vishal recalls
                    # the unit as "Spynel M" but wasn't sure of the band. Confirm and
                    # flip to LWIR if it was actually built around the Spynel-U.
                    "name": "360-SA: 360° Situational Awareness",
                    "period": "2023 -- Present",
                    "bullets": [
                        "Established comprehensive pytest-based unit test suite on HPC cluster, covering KITTI data ingestion, object detection, and tracking pipeline validation",
                        "Designed and deployed Jacamar-CI pipeline to automate build, test, and deployment workflows for vehicle-mounted 360° camera systems",
                        "Engineered hardware solution using TI's SD384EVK board to resolve impedance mismatch between cameras and Wolf Orin computing platform",
                        "Built a custom frame grabber for HGH's Spynel MWIR panoramic thermal camera, bridging its native output into the 360-SA vision pipeline so detection and tracking modules could consume the feed alongside the existing visible-band cameras",
                        "Modernized the 360-SA operator GUI by migrating the legacy tkinter application to PyQt6, adding collapsible and movable sub-windows, individually selectable UI elements, and a layout that matched the requested operator workflow",
                    ],
                },
                {
                    "name": "HACS: Hardware \\& Control System",
                    "period": "2024",
                    "bullets": [
                        "Managed complete lifecycle of custom thermal control PCB: hand-populated 0402 components on milled EagleCAD boards and delivered integrated system for vehicle demo",
                        "Developed C++ firmware for STM32 microcontroller to control thermal switches and stream status data over raw UDP/TCP protocols",
                    ],
                },
                {
                    "name": "GREMLIN: MWIR Video Processing",
                    "period": "2023",
                    "bullets": [
                        "Performed literature review to select optimal model architectures for post-processing of MWIR video datasets",
                        "Designed annotation-repair algorithm that re-labels mis-detections by running data through trained models, extracting metadata, and performing similarity comparison between detections",
                    ],
                },
                {
                    "name": "ENFIRE: Environmental Imaging",
                    "period": "2024 -- Present",
                    "bullets": [
                        "Assembled rugged, portable sensor enclosure housing Jetson Orin, Ouster LiDAR, DAGR receiver, power pack, and network switch/router",
                        "Conducted campus-scale SLAM and point-cloud mapping tests to validate environmental-imaging performance with and without enclosure",
                    ],
                },
                {
                    "name": "DRAGON: Drone Swarm Synchronization",
                    "period": "2024",
                    "bullets": [
                        "Implemented Chrony time synchronization across multi-drone swarm and profiled system resilience under simulated network disruptions",
                    ],
                },
                {
                    "name": "PAAM: AFSIM Simulation Surrogate Modeling",
                    "period": "2024",
                    "bullets": [
                        "Built visualizations and surrogate models for high-dimensional AFSIM simulation data, enabling exploratory analysis of sim outputs and faster iteration than re-running the full simulation for each parameter sweep",
                    ],
                },
                {
                    "name": "SHELAC: Rooftop Meteorological Sensor Deployment",
                    "period": "Nov 2025 -- Present",
                    "bullets": [
                        "Deployed two weather stations and three anemometers along the northern edge of the building roof, running communication cabling from the rooftop through an access hatch into the LIDAR lab machine downstairs",
                        "Sourced all cable stock, connectors, and converters for the install; fabricated and bench-tested the ruggedized Ethernet runs for the weather stations and the serial runs for the anemometers alongside a coworker before on-roof install",
                        "Converted the Young sonic anemometer from RS-232 to RS-485 with an in-line converter to preserve signal integrity over the long cable run, which would otherwise have degraded the serial signal past a usable threshold",
                    ],
                },
            ],
        },
        {
            "org": "Rain Neuromorphics",
            "title": "Electrical Engineering Intern",
            "location": "Gainesville, FL",
            "period": "May 2017 -- May 2018",
            "projects": [
                {
                    "name": None,
                    "period": None,
                    "bullets": [
                        "Designed and tested FPGA-based measurement system with Altera FPGA communicating with Arduino interface for characterizing in-house memristive devices",
                        "Developed and manufactured PCB in EAGLE to house 40 leaky integrate-and-fire neurons, integrating measurement system circuitry",
                        "Analyzed spiking behavior data output from measurement system to benchmark MNIST dataset performance on neuromorphic hardware",
                    ],
                },
            ],
        },
    ],
}


LATEX_TEMPLATE = r"""
\documentclass[11pt, letterpaper]{article}

\usepackage[T1]{fontenc}
\usepackage[margin=0.5in]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage{hyperref}
\usepackage{xcolor}

% ── Formatting ─────────────────────────────────────────────────────────────
\pagestyle{empty}
\setlength{\parindent}{0pt}
\definecolor{linkblue}{HTML}{2563EB}

\hypersetup{
    colorlinks=true,
    urlcolor=linkblue,
    linkcolor=linkblue,
}

\titleformat{\section}{\large\bfseries\color{linkblue}}{}{0em}{}[\titlerule]
\titlespacing*{\section}{0pt}{12pt}{6pt}

\setlist[itemize]{leftmargin=1.2em, itemsep=2pt, parsep=0pt, topsep=2pt}

\begin{document}

% ── Header ─────────────────────────────────────────────────────────────────
\begin{center}
{\LARGE \textbf{<<NAME>>}} \\[4pt]
\small <<EMAIL>> $\cdot$ <<LOCATION>> $\cdot$ \href{https://<<LINKEDIN>>}{<<LINKEDIN>>} $\cdot$ \href{https://<<WEBSITE>>}{<<WEBSITE>>}
\end{center}

% ── Education & Skills ─────────────────────────────────────────────────────
\section{Education \& Technical Skills}

\begin{tabular}{@{}p{4.5cm} p{12.7cm}@{}}
\textbf{Education} & \textbf{<<SCHOOL>>} -- <<DEGREE>> (<<EDU_PERIOD>>) \\[4pt]
<<SKILL_ROWS>>
\end{tabular}

% ── Experience ─────────────────────────────────────────────────────────────
\section{Experience}

<<EXPERIENCE_BLOCKS>>

\end{document}
"""


def _escape_latex(text: str) -> str:
    """Escape special LaTeX characters, preserving already-escaped ones."""
    # Don't double-escape
    if "\\" in text:
        return text
    replacements = [
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _build_skill_rows(skills: dict) -> str:
    """Build LaTeX table rows for skills section."""
    rows = []
    for category, skill_list in skills.items():
        safe_cat = _escape_latex(category)
        rows.append(f"\\textbf{{{safe_cat}}} & {skill_list} \\\\")
    return "\n".join(rows)


def _build_experience_block(exp: dict) -> str:
    """Build LaTeX block for one employer."""
    lines = []
    org = exp["org"]
    title = exp["title"]
    location = exp["location"]
    period = exp["period"]

    lines.append(f"\\textbf{{\\large {org}}} \\hfill {location} \\\\")
    lines.append(f"\\textit{{{title}}} \\hfill \\textit{{{period}}}")

    for proj in exp["projects"]:
        if proj["name"]:
            lines.append(f"\n\\hspace{{0.5em}}\\textbf{{{proj['name']}}} \\textit{{({proj['period']})}}")
        lines.append("\\begin{itemize}")
        for bullet in proj["bullets"]:
            lines.append(f"  \\item {bullet}")
        lines.append("\\end{itemize}")

    return "\n".join(lines)


def generate_tailored_latex(job: dict, tailoring: dict) -> dict:
    """
    Use Claude to select and reorder resume content for a specific job,
    then compile to PDF.

    Args:
        job: Dict with job details
        tailoring: Output from tailor_resume() — emphasis_areas, keywords, etc.

    Returns:
        Dict with latex_source, pdf_path, and compilation status.
    """
    # Load voice profile
    voice_path = Path(__file__).parent.parent / "templates" / "VOICE_PROFILE.md"
    voice_profile = voice_path.read_text(encoding="utf-8") if voice_path.exists() else ""

    profile = ""
    if CANDIDATE_PROFILE_PATH.exists():
        profile = CANDIDATE_PROFILE_PATH.read_text(encoding="utf-8")

    job_title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    job_desc = job.get("description", "")

    prompt = f"""You are tailoring a LaTeX resume for Vishal Pathak for a specific job application.
You have his complete base resume data below. Your job is to SELECT and REORDER content
to best match the target role. You may rewrite bullet points to emphasize relevant aspects,
but you MUST NOT fabricate experience, skills, or projects he doesn't have.

VOICE PROFILE:
{voice_profile}

CANDIDATE PROFILE:
{profile}

BASE RESUME DATA (this is the complete truth — all projects and bullets available):
{json.dumps(BASE_RESUME, indent=2)}

TAILORING GUIDANCE (from earlier analysis):
{json.dumps(tailoring, indent=2)}

TARGET JOB:
Title: {job_title}
Company: {company}
Description: {job_desc}

YOUR TASK — respond with a JSON object containing:

1. "skills" — a dict of 4-5 skill categories with comma-separated skills.
   Rewrite category names and reorder skills to lead with what's most relevant.
   Only include skills he actually has from the base data.

2. "experience" — a list of experience entries. Each entry has:
   - "org", "title", "location", "period" (keep these factual)
   - "projects" — list of projects to INCLUDE (you can drop irrelevant ones).
     Each project has "name" (null for Rain), "period", and "bullets".
     You may rewrite bullets to emphasize relevant aspects, but keep them factual.
     Lead with the most relevant projects for this role.

3. "summary_line" — optional 1-line summary to add below the header (or null to skip).
   If included, write it in Vishal's voice: direct, technical, no fluff.

RULES:
- GTRI projects you can include or exclude based on relevance. Always include at least
  SPARSE and one other. Drop projects that add no value for this specific role.
- Rain Neuromorphics should always be included.
- Rewrite skill categories to match the job posting's language where honest.
- Bullets should be specific and technical. No vague claims.
- Keep the resume to 1 page worth of content (roughly 15-20 bullets total max).
- Do NOT add projects, employers, or skills that don't exist in the base data.

Respond with valid JSON only, no markdown."""

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text.strip()

    # Parse JSON
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    tailored = json.loads(response_text.strip())

    # ── Build LaTeX source ──────────────────────────────────────────────
    latex = LATEX_TEMPLATE
    latex = latex.replace("<<NAME>>", BASE_RESUME["name"])
    latex = latex.replace("<<EMAIL>>", BASE_RESUME["email"])
    latex = latex.replace("<<LOCATION>>", BASE_RESUME["location"])
    latex = latex.replace("<<LINKEDIN>>", BASE_RESUME["linkedin"])
    latex = latex.replace("<<WEBSITE>>", BASE_RESUME["website"])
    latex = latex.replace("<<SCHOOL>>", BASE_RESUME["education"]["school"])
    latex = latex.replace("<<DEGREE>>", BASE_RESUME["education"]["degree"])
    latex = latex.replace("<<EDU_PERIOD>>", BASE_RESUME["education"]["period"])

    # Skills
    skill_rows = _build_skill_rows(tailored.get("skills", BASE_RESUME["skills"]))
    latex = latex.replace("<<SKILL_ROWS>>", skill_rows)

    # Experience
    exp_blocks = []
    for exp in tailored.get("experience", BASE_RESUME["experience"]):
        exp_blocks.append(_build_experience_block(exp))
    latex = latex.replace("<<EXPERIENCE_BLOCKS>>", "\n\n\\vspace{6pt}\n\n".join(exp_blocks))

    # ── Compile to PDF in a tempdir (nothing persists locally) ─────────
    safe_company = "".join(c if c.isalnum() else "_" for c in company)
    pdf_bytes: bytes | None = None
    compile_success = False
    compile_log = ""

    with tempfile.TemporaryDirectory(prefix="latex_resume_") as td:
        td_path = Path(td)
        tex_path = td_path / f"resume_{safe_company}.tex"
        pdf_path = td_path / f"resume_{safe_company}.pdf"
        tex_path.write_text(latex, encoding="utf-8")

        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode",
                 "-output-directory", str(td_path), str(tex_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            compile_success = result.returncode == 0 and pdf_path.exists()
            if not compile_success:
                compile_log = (result.stdout or result.stderr)[-2000:]
                logger.warning(f"LaTeX first pass issue: {compile_log[-500:]}")
                # Second pass (e.g. for references)
                result2 = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode",
                     "-output-directory", str(td_path), str(tex_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                compile_success = result2.returncode == 0 and pdf_path.exists()
                if not compile_success:
                    compile_log = (result2.stdout or result2.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            compile_log = "pdflatex timed out after 30 seconds"
            logger.error(compile_log)
        except FileNotFoundError:
            compile_log = "pdflatex not found — LaTeX not installed"
            logger.error(compile_log)

        if compile_success:
            pdf_bytes = pdf_path.read_bytes()

    logger.info(
        f"LaTeX resume for {company}: compile={'OK' if compile_success else 'FAILED'}, "
        f"bytes={len(pdf_bytes) if pdf_bytes else 0}"
    )

    return {
        "latex_source": latex,
        "pdf_bytes": pdf_bytes,
        "compile_success": compile_success,
        "compile_log": compile_log if not compile_success else "",
        "tailored_data": tailored,
    }
