# Vishal Pathak — Master CV
#
# USER-LAYER. The single source of truth for resume content. Everything
# in `job-applicant/tailor/latex_resume.py::BASE_RESUME` should match this
# file. Tailoring may *select* and *reorder*; it never invents.

**Email:** vishalp@thak.io
**Location:** Atlanta, GA
**LinkedIn:** linkedin.com/in/vishalhpathak
**Website:** vishal.pa.thak.io

## Education

**Florida Institute of Technology** — B.S. Electrical Engineering, *cum laude* (2019–2021)

## Technical Skills

- **Neuromorphic & Simulation:** Intel LavaSDK, NxSDK, Brian2, MuJoCo, Gymnasium API, FlyGym, VHDL, RTL design, AFSIM surrogate modeling
- **Programming & ML:** Python, C/C++, PyTorch, TensorFlow, NumPy, Matplotlib, scikit-learn, PyQt6
- **Systems & Hardware:** FPGA development, embedded systems (STM32), PCB design (EAGLE/Altium), serial protocols (RS-232/RS-485), ruggedized sensor deployment, HPC clusters
- **Tools & Platforms:** Git, CI/CD (Jacamar-CI), pytest, Docker, Linux, MATLAB, LabVIEW

## Experience

### Georgia Tech Research Institute — Algorithms & Analysis Engineer
*Atlanta, GA · August 2021 – Present*

#### SPARSE: Spiking Processing for Autonomous RF & Sensor Engineering (Aug 2021 – Jul 2024)
- Developed VHDL models of CUBA and LIF neurons matching Intel's LavaSDK behavior, enabling seamless deployment of spiking neural networks from simulation to FPGA hardware.
- Deployed and benchmarked custom spiking networks on Intel's Kapoho Bay neuromorphic platform, evaluating power consumption and inference performance for edge applications.
- Contributed to DNN→SNN conversion pipeline using backpropagation in the spiking regime for overhead imagery and radar signal processing applications.
- Trained deep learning models on GTRI's ICEHAMMER HPC cluster using PyTorch and TensorFlow.

#### 360-SA: 360° Situational Awareness (2023 – Present)
- Established comprehensive pytest-based unit test suite on HPC cluster, covering KITTI data ingestion, object detection, and tracking pipeline validation.
- Designed and deployed Jacamar-CI pipeline to automate build, test, and deployment workflows for vehicle-mounted 360° camera systems.
- Engineered hardware solution using TI's SD384EVK board to resolve impedance mismatch between cameras and Wolf Orin computing platform.
- Built a custom frame grabber for HGH's Spynel MWIR panoramic thermal camera, bridging its native output into the 360-SA vision pipeline so detection and tracking modules could consume the feed alongside the existing visible-band cameras.
- Modernized the 360-SA operator GUI by migrating the legacy tkinter application to PyQt6 with collapsible/movable sub-windows, individually selectable UI elements, and a layout matching the requested operator workflow.

#### HACS: Hardware & Control System (2024)
- Managed complete lifecycle of custom thermal control PCB: hand-populated 0402 components on milled EagleCAD boards and delivered integrated system for vehicle demo.
- Developed C++ firmware for STM32 microcontroller to control thermal switches and stream status data over raw UDP/TCP protocols.

#### GREMLIN: MWIR Video Processing (2023)
- Performed literature review to select optimal model architectures for post-processing of MWIR video datasets.
- Designed annotation-repair algorithm that re-labels mis-detections by running data through trained models, extracting metadata, and performing similarity comparison between detections.

#### ENFIRE: Environmental Imaging (2024 – Present)
- Assembled rugged, portable sensor enclosure housing Jetson Orin, Ouster LiDAR, DAGR receiver, power pack, and network switch/router.
- Conducted campus-scale SLAM and point-cloud mapping tests to validate environmental-imaging performance with and without enclosure.

#### DRAGON: Drone Swarm Synchronization (2024)
- Implemented Chrony time synchronization across multi-drone swarm and profiled system resilience under simulated network disruptions.

#### PAAM: AFSIM Simulation Surrogate Modeling (2024)
- Built visualizations and surrogate models for high-dimensional AFSIM simulation data, enabling exploratory analysis of sim outputs and faster iteration than re-running the full simulation for each parameter sweep.

#### SHELAC: Rooftop Meteorological Sensor Deployment (Nov 2025 – Present)
- Deployed two weather stations and three anemometers along the northern edge of the building roof, running communication cabling from the rooftop through an access hatch into the LIDAR lab machine downstairs.
- Sourced all cable stock, connectors, and converters for the install; fabricated and bench-tested the ruggedized Ethernet runs for the weather stations and the serial runs for the anemometers alongside a coworker before on-roof install.
- Converted the Young sonic anemometer from RS-232 to RS-485 with an in-line converter to preserve signal integrity over the long cable run, which would otherwise have degraded the serial signal past a usable threshold.

### Rain Neuromorphics — Electrical Engineering Intern
*Gainesville, FL · May 2017 – May 2018*

- Designed and tested FPGA-based measurement system with Altera FPGA communicating with Arduino interface for characterizing in-house memristive devices.
- Developed and manufactured PCB in EAGLE to house 40 leaky integrate-and-fire neurons, integrating measurement system circuitry.
- Analyzed spiking behavior data output from measurement system to benchmark MNIST dataset performance on neuromorphic hardware.
