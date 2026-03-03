# Vision Runner (PR-V3)

PR-V3 extends the local runner with a real detector + tracker pipeline while keeping PR-V2 transport behavior:
- reads frames from `webcam:<index>` or `file:<path>`
- pushes JPEG frames to backend `POST /api/frame`
- sends VIS datagrams to backend over UDP (`127.0.0.1:9003`)

## Install

```bash
python -m pip install -r src/vision/requirements.txt
```

## Modes

`--mode pattern|detect` is available, with default `detect`.

### Detect mode (YOLO + tracker)

```bash
python -m src.vision.main --source webcam:0 --mode detect --model-path yolov8n.pt
```

### Pattern mode (PR-V2 synthetic patterns)

```bash
python -m src.vision.main --source webcam:0 --mode pattern --pattern sweep
```

## Common transport flags (unchanged)
- `--backend-http` (default `http://127.0.0.1:8000`)
- `--backend-frame-endpoint` (default `/api/frame`)
- `--vis-udp-host` / `--vis-udp-port` (default `127.0.0.1:9003`)
- `--vis-hz` (default `20`)
- `--frame-fps` (default `10`)
- `--jpeg-quality` (default `80`)
- `--no-frame-push` disables HTTP frame push
- `--no-vis-udp` disables UDP VIS send
- `--no-output` disables stdout publishing

## Detect mode flags
- `--model-path` (default `yolov8n.pt`)
- `--target-class` (default `0`, person in COCO)
- `--conf-threshold` (default `0.5`)
- `--tracker none|kcf|csrt` (default `kcf`)
- `--infer-width` (default `640`, aspect-preserving)
- `--infer-size` (optional square size, overrides `--infer-width`)
- `--detect-every-n` (default `1`)
- `--detect-hold-n` (default `30`)
- `--search-n` (default `30`)
- `--desired-cx` / `--desired-cy` (default `0.5`, `0.5`)

## Pattern mode flags
- `--pattern none|sweep|lose|reacquire` (default `none`)

## Notes
- Detect mode fails fast with a clear error if `ultralytics` is missing.
- Pattern mode does not require `ultralytics`.
- VIS UDP payload semantics stay strict: if state is not `Tracking`, `loc_x/loc_y/bound_w/bound_h/confidence` are sent as exact `0.0`.
