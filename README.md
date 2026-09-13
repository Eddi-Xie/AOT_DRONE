# AOT_DRONE

**Software for a quadcopter that tracks and follows a target on its own: a 50 Hz control loop on a Raspberry Pi, a Python vision pipeline, and a ground station that runs in the browser.**

> **Status:** paused, late in Sprint 0 (software hardening). Motors stayed off for the whole sprint; the MSP command path is bench-tested against the real Betaflight board with the motors detached, and everything else runs against recording or fake sinks. See [Current status](#current-status).

---

## What this is

AOT_DRONE is a quadcopter that finds an object in its camera feed and flies to keep it centred. Most of the project is software. A hand-written control layer handles takeoff, tracking and landing as a state machine, sends RC commands to a Betaflight flight controller over MSP at 50 Hz, and gets its targets from a Python vision pipeline. A web gateway and a React app make up the ground station.

It started as the flight-control half of a UBC MECH410G capstone project and kept going after the course ended. I own the flight-control stack, the vision integration, and the ground-station plumbing.

I wanted to write the autonomy layer myself instead of configuring an existing autopilot. Every mode, transition and safety gate here is code I've put thought into, not external code I don't understand.

## Demo

[![Tracking demo video](https://img.youtube.com/vi/yqiiV8uiTjg/maxresdefault.jpg)](https://youtu.be/yqiiV8uiTjg)

**[Watch the tracking demo on YouTube](https://youtu.be/yqiiV8uiTjg)**: the vision pipeline and ground station running live on the bench. Flight footage gets added once this stack flies.

---

## Architecture

Four processes, kept separate so any one of them can run or be tested on its own. They all meet in the backend gateway. The vision process never talks to the flight-control process directly.

```text
┌───────────────────────┐                  ┌───────────────────────┐
│  src/vision/          │                  │  src/webapp/          │
│  Python               │                  │  React + TS + Vite    │
│  YOLOv8n + KCF/CSRT   │                  │  ground station       │
└──────────┬────────────┘                  └──────────▲────────────┘
           │ VIS target state (UDP :9003)             │ REST + WebSocket
           │ JPEG frames (HTTP POST)                  │ + MJPEG /video
           ▼                                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  src/backend/  -  FastAPI gateway                               │
│  telemetry/vision in → browser out;  operator intent → FC       │
└──────────┬───────────────────────────────────▲──────────────────┘
           │ CMD stream (TCP :9002, 50 Hz;     │ TEL (UDP :9001, 50 Hz)
           │ backend connects, FC listens)     │
           ▼                                   │
┌─────────────────────────────────────────────────────────────────┐
│  src/fc/  -  C++ autonomy layer (Raspberry Pi)                  │
│  50 Hz loop · Manual/Takeoff/Tracking/LandSafely state machine  │
│  fail-closed safety gates · swappable RC sink                   │
└──────────┬──────────────────────────────────────────────────────┘
           │ MSPv1 over USB serial
           │ MSP_SET_RAW_RC @ 50 Hz · MSP_RC readback @ 5 Hz
           ▼
┌───────────────────────┐
│  Betaflight FC        │ → ESCs → motors
└───────────────────────┘
```

One naming thing, since it trips people up: `src/fc/` is *my* flight-control layer, the autonomy brain that runs on the Raspberry Pi. The Betaflight flight controller is a separate hardware board underneath it that handles the fast attitude loop. My layer sends it RC-style commands over MSP and never touches the motors directly.

| Component | Language | Responsibility |
| --- | --- | --- |
| `src/fc/` | C / C++ | 50 Hz control loop, flight-mode state machine, safety gates, MSPv1 transport, swappable RC sink (`null` / `recording` / `fake` / `msp`) |
| `src/vision/` | Python | YOLOv8n detection plus OpenCV single-object tracking, turns pixel-space target position into normalized target state for the loop |
| `src/backend/` | Python (FastAPI) | Web gateway. UDP telemetry and vision ingest, a 50 Hz TCP command bridge to the FC with a vision-freshness gate on Tracking, WebSocket broadcast, MJPEG video |
| `src/webapp/` | React + TypeScript | Ground station UI. Mode and arm controls, live video with a bounding-box overlay, telemetry and link status |

Detection is YOLOv8n (ultralytics), person class by default. Between detections an OpenCV KCF or CSRT tracker carries the box, and a four-state machine (NoTarget, TargetDetected, Tracking, Searching) decides when to trust a detection, when to coast on the tracker, and when to give up and go looking again. Tracker re-inits are gated on IoU, and confidence decays on frames where only the tracker has an answer. Bench runs use a laptop webcam or recorded video. *(Onboard Pi frame rate: not measured yet.)*

The wire contract lives in [docs/message-spec.md](docs/message-spec.md): `TEL` (FC to backend, UDP :9001), `VIS` (vision to backend, UDP :9003) and `CMD` (backend to FC, TCP :9002 with length-prefixed framing), all versioned JSON.

### Hardware

| Item | Detail |
| --- | --- |
| Companion computer | Raspberry Pi 4, runs `src/fc/`, `src/vision/` and `src/backend/` |
| Flight controller | Radiolink 722 running Betaflight 4.6, driven over USB MSP. Betaflight 4.6 has position hold, which we'd like but can't use without a GPS. Altitude hold is the mode we plan to lean on, though it's been buggy for us in the past. If arming over USB turns out not to work, the fallback is the Pi's UART pins. |
| Frame | Custom quad-X frame, CAD-modelled and fabricated in-house |
| Propulsion | BE2204 2300KV motors, BLS G0670 ESC, 6x4.5RB direct-drive pusher props |
| Power | MiniStar 4S LiPo, 14.8 V, 450 mAh, 70C |
| Radio | FlySky FS-i6X transmitter and receiver, kept as the manual override and kill path |
| Camera | Arducam camera module on the Pi. Bench work uses a laptop webcam. |

---

## What works today

- A 50 Hz control loop with a proper flight-mode state machine. Manual, Takeoff, Tracking and LandSafely, booting into LandSafely. Inside Tracking there are hover-search, hover-still and follow-target behaviours. Tick pacing is drift-corrected and `dt` is clamped every tick.
- The MSP command path, bench-tested against the real Betaflight board over USB with the motors detached. MSPv1 `MSP_SET_RAW_RC` goes out at 50 Hz in AETR order, and a 5 Hz `MSP_RC` readback poll stands in for the Configurator's Receiver tab. The first bench session caught a channel-ordering bug that the unit tests had pinned wrong. More on that below.
- Vision in the loop. Tracker output flows from vision to the backend to the FC inside the 50 Hz command stream, and the backend won't forward a Tracking request unless the vision data is fresh, 0.25 s by default.
- The ground station. A React webapp behind the FastAPI gateway, with four mode buttons plus arm and disarm, MJPEG video with a bounding-box overlay, telemetry panels and link-status badges. WebSocket events are throttled to 20 Hz.
- Safety interlocks that fail closed. Uncalibrated hover throttle blocks entry into Takeoff and Tracking. A stale command link (0.5 s) or a degraded RC sink drops the FC into LandSafely. Disarm forces minimum throttle every tick, and LandSafely ramps down and auto-disarms.
- Bench calibration tooling. [scripts/dev/hover_calibration.py](scripts/dev/hover_calibration.py) sweeps throttle from 1000 to 1700 µs in 10 µs steps, 71 samples held for a second each. The operator classifies each step as liftoff, stable, clipping or skip, and the script writes a CSV for later lift-off classification. Ctrl-C resets throttle before exiting.
- CI on every push. One GitHub Actions job runs pre-commit (ruff, ruff-format, clang-format), a CMake build, ctest with 9 C++ test binaries, and pytest with about 190 tests across 47 files, some of which drive the real `fc_app` binary as a subprocess. Then the webapp's vitest suite and a `tsc` plus Vite build. Python 3.10, Node 20.

## What doesn't work yet

- **This stack has never flown.** The airframe has. It flew manual flights on the previous-generation stack. The redesigned stack in this repo has only ever run on the bench with the motors detached, and its first flight sits behind a 16-item pre-flight gate in [docs/development_plan.md](docs/development_plan.md) that is currently 0 for 16 on purpose. An ADR forbids flying, even tethered, until the whole list is green.
- Hover throttle is uncalibrated. `FC_HOVER_THROTTLE` defaults to `0`, which the FC treats as unknown and refuses to fly on.
- No slew limiting on control outputs yet (S0.10), so a channel can legally step ±1000 µs in a single 20 ms tick.
- Vision input is unfiltered (S0.11). There is no outlier rejection or smoothing before detector output reaches the loop.
- The yaw PID is untuned (S0.12), and altitude hold works but needs polish (S0.13).
- Arm authority isn't gated yet (S0.14). Right now the FC applies the operator's arm command unconditionally. The MSP arm-switch readback exists but nothing reads it yet. The planned gate needs the operator command, fresh tracking and the physical RC switch all at the same time.
- Arming over USB is unproven. Betaflight might refuse to arm on a USB-CDC connection. If it does, the fallback is the Pi's UART pins in Sprint 1.

---

## Where to look if you're reading the code

**1. The hover-throttle gate (`src/fc/`)**
Hover throttle comes from `FC_HOVER_THROTTLE` and defaults to `0`, which means uncalibrated rather than zero throttle. In that state the mode gate rejects any transition into Takeoff or Tracking. If calibration is somehow lost while one of those modes is already running, a per-tick guard drops to LandSafely on the same tick. The clamp path also protects the sentinel. Any non-zero value gets clamped into the 1000 to 1800 µs band, but zero passes through untouched, so the safe default can't accidentally become a flyable value. If you forget to configure the vehicle, it won't take off.

**2. Clamp vs ceiling in altitude hold**
Altitude hold originally used one constant, `1500`, as both the runtime output clamp and the maximum allowable throttle. Those are two different limits. One is about control authority and the other is about safety, and merging them silently capped climb authority at the clamp value. They're split now, with the ceiling at `1800`, which leaves 200 µs of margin below the RC channel max of 2000.

**3. The channel-order bug the bench caught (`MspRcSink`)**
MSP writes take channels in AETR order, but `MSP_RC` reads them back in Betaflight's internal RPYT order, and that asymmetry isn't obvious from the protocol docs. The first version wrote RPYT. The unit test pinned the buggy order, so CI was green. Then the first bench run against the real board showed the FC reporting about 50% throttle on the yaw channel while the stack thought it was sending disarm-safe values. With props on that would have been dangerous. On the bench it cost an afternoon. The fix and the asymmetry are both written up in the code and in [docs/hil.md](docs/hil.md) so nobody "fixes" the apparent swap back later.

**4. Bench-first calibration (`scripts/dev/hover_calibration.py`)**
Instead of finding hover throttle in the air, the sweep runs on the bench against a recording sink, with an operator prompt at every step, and produces a CSV that Sprint 1 uses for lift-off classification. One caveat, noted in the script itself: the `observed_us` column records what `fc_app` sent after clamping, not what the Betaflight board echoed back. The MSP_RC echo extension comes in Sprint 1.

---

## Repo layout

```text
AOT_DRONE/
├── src/
│   ├── fc/            # C/C++ flight control: 50 Hz loop, state machine, MSP, RC sinks
│   ├── vision/        # Python: YOLOv8n detection + OpenCV tracking
│   ├── backend/       # Python FastAPI gateway: telemetry out, commands in, video
│   └── webapp/        # React + TypeScript + Vite ground station
├── scripts/dev/       # bench + demo tooling (hover_calibration.py, demo_up.sh, runall.sh, ...)
├── tests/             # 47 pytest files; tests/fc/ has 9 C++ ctest binaries; tests/integration/ is e2e
├── docs/
│   ├── architecture.md       # system overview
│   ├── message-spec.md       # wire contract, the source of truth for ports/enums/framing
│   ├── hil.md                # hardware-in-the-loop bench design and procedures
│   ├── decisions.md          # architecture decision records
│   └── development_plan.md   # sprint backlog + dated progress log
├── CMakeLists.txt
└── yolov8n.pt         # detector weights used by the demo
```

Day-to-day work lives in [docs/development_plan.md](docs/development_plan.md): numbered sprint tasks (S0.1 to S0.23), a Sprint 1 hardware plan (H1 to H7), and a dated progress log. If you want the real state of the project, read that file.

---

## Running it

> ### ⚠️ Safety
>
> **Props off. Every time.** Nothing in this repo should run on a powered vehicle with propellers attached unless you're deliberately doing a flight test, with a manual override and a kill switch in reach.
>
> Bench work uses the `recording` or `fake` sinks, not live ESCs. The `msp` sink talks to the real Betaflight board and is only used with the motors detached. The calibration script is bench-only and prompts the operator at every step.
>
> `FC_HOVER_THROTTLE=0` is the correct default. Don't set it to a guessed value just to get past the check.

**Prereqs:** Python 3.10, Node 20, CMake 3.16 or newer, and a C++17 compiler. CI runs on Ubuntu, development happens on macOS.

### One-time setup

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip==24.3.1
python -m pip install -r requirements-dev.txt
python -m pip install -r src/backend/requirements.txt
python -m pip install -r src/vision/requirements.txt

npm --prefix src/webapp install
cmake -S . -B build && cmake --build build -j
```

### Vision on a recorded video (fastest way to see it do something)

```bash
python -m src.vision.main --source file:path/to/clip.mp4 --mode detect \
  --model-path yolov8n.pt --target-class 0 --preview
```

`--source webcam:0` for a live camera; `q` or ESC closes the preview.

### The whole stack

```bash
./scripts/dev/demo_up.sh       # backend :8000, fc_app, vision, webapp :5173
./scripts/dev/demo_status.sh
./scripts/dev/demo_down.sh
```

`demo_up.sh` preflight-checks the venv, node_modules, the `fc_app` binary and ports, writes logs to `logs/demo_*.log`, and supports partial startup via `DEMO_SKIP_BACKEND` / `DEMO_SKIP_FC` / `DEMO_SKIP_VISION` / `DEMO_SKIP_WEBAPP`.

### Tests

```bash
./scripts/dev/runall.sh              # the CI-equivalent gate: lint + pytest + ctest + webapp
pytest -q                            # Python suite
ctest --test-dir build --output-on-failure   # C++ suite
npm --prefix src/webapp run test     # webapp (vitest)
```

### FC environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `FC_RC_SINK` | `null` | RC output sink: `null` / `recording` (CSV) / `fake` (UDP JSON) / `msp` (real board). Unknown values refuse to start. |
| `FC_RC_DEVICE` | — | Serial device for `msp` (required; must be a character device, e.g. `/dev/tty.usbmodem*`) |
| `FC_RC_BAUD` | `115200` | Serial baud (allowlisted standard rates) |
| `FC_RC_LOG_DIR` | `logs/hil` | Where `recording` writes its per-tick CSV |
| `FC_RC_FAKE_HOST` / `FC_RC_FAKE_PORT` | `127.0.0.1` / `9101` | Target for the `fake` sink |
| `FC_BIND_HOST` | `127.0.0.1` | Bind address for the TCP command listener |
| `FC_TEL_HOST` / `FC_TEL_PORT` | `127.0.0.1` / `9001` | UDP telemetry destination |
| `FC_HOVER_THROTTLE` | `0` (uncalibrated) | `0` or 1000–1800 µs; anything else refuses to start |

Backend (`BACKEND_*`), vision (`VISION_*`) and webapp (`VITE_*`) knobs are documented in [docs/message-spec.md](docs/message-spec.md) and the per-component sources. Ports default to TEL :9001, CMD :9002, VIS :9003 and HTTP :8000, all bound to loopback.

### Calibration (bench, motors detached)

```bash
# terminal 1 — FC against the recording sink
FC_RC_SINK=recording ./build/src/fc/fc_app
# terminal 2 — backend
python -m uvicorn src.backend.app:app --host 127.0.0.1 --port 8000
# terminal 3 — the sweep
python scripts/dev/hover_calibration.py --log-dir logs/hil
```

The script drives Manual-mode throttle setpoints through `POST /api/intent`, prompts for a classification at each of the 71 steps, and writes a CSV of `(step_idx, requested_us, observed_us, classification, notes)`. `--unattended` skips the prompts for CI smoke runs.

---

## Current status

**Sprint 0, software hardening.** Motors stayed off for the whole sprint. The goal was that when they come back on, nothing surprising happens. The project is parked here for now, and this section is the state it will resume from.

Everything through S0.9 (hover throttle calibration) is merged. One S0.9 item lives on the bench rather than in code and stays open: an acceptance run of the calibration script with the FC plugged in, confirming a clean 71-step CSV.

Still open in Sprint 0 (P1):

| ID | Task |
| --- | --- |
| S0.10 | Slew limiter on control outputs |
| S0.11 | Vision input filtering |
| S0.12 | Yaw PID tuning |
| S0.13 | Altitude-hold polish |
| S0.14 | Arm-authority gate |

**Sprint 1 is when the motors come back on.** Physical build-out (mounts, harness, power), real-FC HIL acceptance on a test stand, and the calibration CSV put to use for real lift-off classification. Once the pre-flight gate is green, a seven-step progression runs from tethered hover up to the first flight under this stack.

Branches: `dev` is the integration branch. `main` is intentionally stale.

---

## Engineering notes

**Why write the autonomy layer instead of using ArduPilot or PX4.** Two reasons: control and education. I wanted to own and understand every mode, transition and safety gate in the system, not configure someone else's autopilot. We did consider going one level deeper and writing the flight-control firmware ourselves instead of flashing Betaflight, but the time and complexity weren't worth it, so Betaflight keeps the fast attitude loop and this stack commands it over MSP.

**Why a software-first sprint with the motors off.** Hardening the code first means that when hardware problems show up later, they are actually hardware problems, not a tangle of both. It was also practical: software could be worked on solo at flexible hours, while hardware changes needed tools, space and meeting up. The approach paid off on the very first bench session, when the real board exposed a channel-ordering bug (AETR out, RPYT back) that the unit tests had pinned wrong. On the bench it cost an afternoon. With props on it would have been dangerous.

**Why the hover-throttle default is 0 and not a plausible value.** The FC ships uncalibrated and refuses to enter any flying mode that way. An earlier build defaulted to 1100 µs, and an audit flagged it: a forgotten config should produce a refusal on the bench, not a takeoff attempt on a guessed number.

---

## Project context

Built for **MECH410G** at UBC (Sept to Dec 2025), continued independently after the course ended. I own the software stack: the C++ control layer, the vision-to-control integration, MSP transport and the ground-station plumbing. My partner led the hardware and fabrication side. An earlier version of this vehicle flew manually on the old stack, and this repo is the software redesign that came out of what we learned building it.

We kept going past the course because we were genuinely excited about the project. Life eventually got in the way: my partner ran out of time to continue, the project was handed to me, and with it went access to the lab and fabrication tools, which limited the hardware side. The redesigned software stack got finished and bench-proven instead. It's parked here for now, and I intend to come back to it.

## License

Apache License 2.0. See [LICENSE](LICENSE).
