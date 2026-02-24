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

## Current legacy message formats (to be standardized)

Telemetry:
`TEL <timestamp_s> <control_mode> <tracking_state> <distFront_m> <distBack_m> <distBottom_m> <target_x> <target_y> <bound_w> <bound_h> <confidence>`

Vision:
`<state> <loc_x> <loc_y> <bound_w> <bound_h> <confidence> <timestamp_s>`
