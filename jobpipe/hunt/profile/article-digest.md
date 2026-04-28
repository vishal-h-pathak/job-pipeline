# Vishal Pathak — Article Digest (Proof Points + Metrics)
#
# USER-LAYER. Curated set of "claim → evidence" pairs that downstream
# tailoring + cover-letter prompts can pull from. The detector script
# `cv_sync_check.py` (J-9) flags drift between the claims here and the
# numbers in `cv.md`, the LaTeX template, and `CLAUDE.md`.

## The narrative through-line

Hodgkin–Huxley → memristors → spiking networks → connectomics. The
emergence question is what links them: how RC-circuit ion channels scale
into cognition. Every job and project below is a step along that thread.

## Rain Neuromorphics (2017–2018)

- **Employee #5 at age 19.** Worked on memristive hardware development
  hands-on — building leaky integrate-and-fire neuron PCBs, writing
  measurement software for in-house memristive devices.
- **Built a 40-LIF-neuron PCB** in EAGLE, populated by hand, integrated
  with FPGA-based measurement system (Altera FPGA + Arduino interface).
- **Benchmarked MNIST** on the neuromorphic hardware — first end-to-end
  bench measurement of spiking behavior on the in-house devices.

## GTRI (2021–Present, ~5 years)

### SPARSE — neuromorphic deployment to silicon

- **Wrote VHDL models of CUBA and LIF neurons** that matched Intel
  LavaSDK behavior, enabling sim-to-FPGA portability for spiking
  networks. This is the core neuromorphic hardware credibility claim.
- **Deployed SNNs to Intel Kapoho Bay (Loihi 1/2)** and benchmarked
  power vs GPU baselines for edge applications.
- **Contributed to a DNN→SNN conversion pipeline** using backprop in the
  spiking regime — overhead imagery and radar signal processing
  applications. (Cite this when the JD mentions DNN→SNN, conversion, or
  spike-rate coding.)
- **Trained on the ICEHAMMER HPC cluster** with PyTorch + TensorFlow —
  cite when JD mentions HPC or distributed training.

### 360-SA — embedded ML + computer vision + UI

- **pytest suite on HPC** covering KITTI ingestion, detection, tracking.
- **Jacamar-CI pipeline** for vehicle-mounted 360° camera systems.
- **Custom frame grabber for HGH's Spynel MWIR thermal camera** —
  bridging its native output into the existing 360-SA vision pipeline
  alongside visible-band cameras.
- **PyQt6 migration of the operator GUI** from legacy tkinter, with
  collapsible/movable sub-windows. Cite when the JD wants desktop GUI
  development.

### HACS — embedded hardware + firmware

- **STM32 thermal-control PCB hand-populated** with 0402 components on
  milled EagleCAD boards. Delivered integrated system for vehicle demo.
- **C++ firmware** for thermal switches over raw UDP/TCP.

### Other relevant GTRI projects

- **ENFIRE** — ruggedized sensor enclosure (Jetson Orin, Ouster LiDAR,
  DAGR receiver) plus campus-scale SLAM tests.
- **PAAM** — AFSIM simulation surrogate models, high-dim parameter
  sweep visualization. Cite when the JD mentions surrogate modeling or
  simulation-based optimization.
- **DRAGON** — Chrony time-sync across drone swarm under simulated
  network disruptions.
- **SHELAC** — rooftop weather/anemometer install, RS-232 → RS-485
  in-line conversion to preserve signal integrity over long cable runs.

## Personal projects

- **Trading agent** — autonomous, multi-agent system using Claude. The
  job-hunter and job-applicant codebases are sister projects. Both are
  full-stack agentic AI projects with real Postgres-backed state and
  Playwright-driven submission. Cite when the JD wants demonstrated
  agentic AI experience or full-stack delivery.
- **FlyGym + connectomics work** — embodied simulation experiments
  using the Drosophila connectome. Cite when the JD mentions embodied
  simulation, connectomics, or biologically-grounded RL.

## Soft-skill / SE-relevant proof points

- **Pitched to DoD program sponsors** — built demos to secure continued
  funding. This is the most credible analogue to pre-sales / post-sales
  solutions engineering in his history.
- **Translated complex research into stakeholder-friendly deliverables**
  — write-ups, demos, and progress reviews for non-technical readers.
- **Customer-facing communication comfort** even without a formal SE
  title.

## Metrics we are confident about

- ~7+ years across neuromorphic, SNN, embedded ML.
- ~4 years at GTRI specifically.
- ~1 year at Rain Neuromorphics as employee #5 starting at age 19.
- 40 LIF neurons on the Rain measurement PCB.
- Two weather stations + three anemometers on the SHELAC install.

## Metrics we DO NOT have (do not invent)

- Latency reductions, throughput numbers, or accuracy deltas for
  published projects unless they appear above. The "3x power reduction
  at 94% mAP" line in the resume tailoring prompt is illustrative only
  — never claim a specific number unless it's listed here or in `cv.md`.
