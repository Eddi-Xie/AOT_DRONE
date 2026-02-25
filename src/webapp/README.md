# Webapp (PR-W1 + PR-W2)

React control panel for backend WS updates (`/ws`) and operator intents (`POST /api/intent`).
PR-W2 adds video plumbing with canvas overlays driven by `VIS_UPDATE` (default) or `TEL_UPDATE`.

## Install

From repo root:

```bash
cd src/webapp
npm install
```

## Run (dev)

```bash
npm run dev
```

Vite default URL: `http://127.0.0.1:5173`.

## Backend URL configuration

Set environment variables before starting Vite:

```bash
export VITE_BACKEND_HTTP_URL=http://127.0.0.1:8000
export VITE_BACKEND_WS_URL=ws://127.0.0.1:8000/ws
export VITE_VIDEO_URL=/video
export VITE_OVERLAY_SOURCE=VIS
export VITE_DEBUG_OVERLAY=0
npm run dev
```

Defaults when env vars are not set:
- HTTP target: `http://127.0.0.1:8000`
- Dev WS URL: same-origin `ws(s)://<vite-host>/ws` (proxied by Vite)
- Production WS URL: derived from HTTP (`ws://.../ws` or `wss://.../ws`)
- Video URL: `/video`
- Overlay source: `VIS` (supports `VIS` or `TEL`)
- Overlay debug checks: disabled by default (`VITE_DEBUG_OVERLAY=1` enables geometry assertions)

## Dev proxy behavior

`vite.config.ts` proxies `/api`, `/ws`, and `/video` to `VITE_BACKEND_HTTP_URL` (default `http://127.0.0.1:8000`).
In dev mode, intent POSTs use `/api/intent` through this proxy, avoiding browser CORS issues when UI runs on port `5173`.

## Video stream expectations

The video panel uses an `<img>` stream source (`VITE_VIDEO_URL`) and expects MJPEG over HTTP:

- Endpoint path example: `/video`
- Content type: `multipart/x-mixed-replace; boundary=<boundary>`
- Repeated JPEG frame parts

## Running without video

If no MJPEG endpoint exists yet, overlays still work:

- Use `VITE_VIDEO_URL=mock` (or `none`) to force placeholder mode.
- Or keep `/video`; if stream connection fails the UI falls back to a "No video stream" placeholder while overlay data continues rendering.

## Build

```bash
npm run build
npm run preview
```

Typecheck note:
- Build uses `tsc --noEmit` before Vite bundling. This keeps type coverage for app code while avoiding generated TS outputs in the webapp root.

## End-to-end smoke checklist

1. Start backend:

```bash
python -m uvicorn src.backend.app:app --host 127.0.0.1 --port 8000
```

2. Start FC app (optional but useful for realistic `LINK_STATUS`):

```bash
./build/src/fc/fc_app
```

3. Start vision replay (optional, for VIS traffic):

```bash
python scripts/dev/vision_replay.py --pattern sweep
```

4. Start webapp:

```bash
cd src/webapp
npm run dev
```

5. Confirm in UI:
- WS badge turns `Connected`.
- `LINK_STATUS` fields update continuously (default heartbeat ~2 Hz).
- `VIS_UPDATE` data appears when replay is running.
- Clicking mode buttons changes backend command status (`cmd_tx_ok` increments in status/API).
- Video panel appears with a 16:9 stage and overlay label text (`source`, `state`, `confidence`).
- Bounding box overlay follows incoming normalized VIS/TEL data and remains aligned while resizing the browser.

## Backend follow-up (PR-B5)

Current UI video path expects a backend endpoint at `/video`; if absent the panel cleanly falls back to placeholder mode.

Suggested backend follow-up:
- Add a minimal MJPEG endpoint (`multipart/x-mixed-replace`) at `/video` from vision frames, or
- Add a placeholder `/video` endpoint that returns `200` with a clear capability message.
