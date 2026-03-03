# Vision Runner (PR-V2)

PR-V2 extends the local runner so Vision can:
- read real frames from `webcam:<index>` or `file:<path>`
- push JPEG frames to backend `POST /api/frame`
- send VIS datagrams to backend over UDP (`127.0.0.1:9003`)

It keeps PR-V1 stdout output (`VIS` JSON lines) unless disabled.

## Run

Install vision runtime dependencies first:

```bash
python -m pip install -r src/vision/requirements.txt
```

```bash
python -m src.vision.main --source webcam:0 --pattern sweep
```

Common options:
- `--backend-http` (default `http://127.0.0.1:8000`)
- `--backend-frame-endpoint` (default `/api/frame`)
- `--vis-udp-host` / `--vis-udp-port` (default `127.0.0.1:9003`)
- `--vis-hz` (default `20`)
- `--frame-fps` (default `10`)
- `--jpeg-quality` (default `80`)
- `--pattern none|sweep|lose|reacquire` (default `none`)
- `--no-frame-push` disables HTTP frame push
- `--no-vis-udp` disables UDP VIS send
- `--no-output` disables stdout publishing

Supported env overrides:
- `VISION_BACKEND_HTTP`
- `VISION_VIS_UDP_HOST`
- `VISION_VIS_UDP_PORT`
- `VISION_VIS_HZ`
- `VISION_FRAME_FPS`
- `VISION_JPEG_QUALITY`

`BACKEND_VIDEO_MAX_JPEG_BYTES` is respected for frame-size enforcement (default `200000`).

## End-to-End Smoke Checklist

1. Start backend:

```bash
uvicorn src.backend.app:app --host 127.0.0.1 --port 8000 --reload
```

2. Start webapp and open the UI:

```bash
cd src/webapp
npm install
npm run dev
```

3. Run vision on the laptop:

```bash
cd /Users/eddixie/Documents/VSCode/UBC/AOT_DRONE_V2/AOT_DRONE
python -m src.vision.main --source webcam:0 --pattern sweep
```

4. Live smoke commands:

```bash
curl -s http://127.0.0.1:8000/api/status
curl -s http://127.0.0.1:8000/api/status
```

`frames_rx_ok` should increase across calls while Vision is running.

5. Verify:
- UI video panel displays backend `/video` frames (not synthetic fallback/404).
- `/api/status` shows `frames_rx_ok` increasing and `frames_rx_bad` stable.
- `VIS_UPDATE` values arrive and tracking summary updates.
- Overlay box moves as the sweep pattern runs.
