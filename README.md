# AOT_DRONE
Autonomous Object Tracking Drone

# Drone Stack (C + Python + Web)

Monorepo for the drone control stack:
- `src/fc`: C/C++ flight controller comms and control loop
- `src/vision`: Python vision + tracking
- `src/backend`: Web gateway (WebSocket/REST)
- `src/webapp`: React UI

## Docs
- `docs/architecture.md`: system overview
- `docs/message-spec.md`: message contract (source of truth)
- `docs/setup.md`: build/run instructions
- `docs/decisions.md`: architecture decision records (ADRs)

## Reproducibility
- Pinned Python tooling: `requirements-dev.txt`
- Pinned backend runtime deps: `src/backend/requirements.txt`
- CI-equivalent local check command: `./scripts/dev/runall.sh`

## Vision Local Runner (PR-V1)
Run the local vision scaffold (stub detector/tracker) with newline-delimited VIS JSON output:

```bash
python -m src.vision.main --source webcam:0 --preview
python -m src.vision.main --source file:assets/test.mp4 --vis-hz 20 --max-frames 300
```

Optional outputs:
- `--no-output` disables stdout VIS emission
- `--output jsonl:logs/vis.jsonl` writes VIS messages to a JSONL file
- `--vis-hz` uses monotonic time-based throttling (first frame emits immediately)

Run vision/test commands from the repository root so `src.*` imports resolve consistently.

## Current legacy message formats (to be standardized)

Telemetry:
`TEL <timestamp_s> <control_mode> <tracking_state> <distFront_m> <distBack_m> <distBottom_m> <target_x> <target_y> <bound_w> <bound_h> <confidence>`

Vision:
`<state> <loc_x> <loc_y> <bound_w> <bound_h> <confidence> <timestamp_s>`
