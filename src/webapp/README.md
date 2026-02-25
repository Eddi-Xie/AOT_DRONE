# Webapp (PR-W1)

React control panel for backend WS updates (`/ws`) and operator intents (`POST /api/intent`).

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
npm run dev
```

Defaults when env vars are not set:
- HTTP target: `http://127.0.0.1:8000`
- Dev WS URL: same-origin `ws(s)://<vite-host>/ws` (proxied by Vite)
- Production WS URL: derived from HTTP (`ws://.../ws` or `wss://.../ws`)

## Dev proxy behavior

`vite.config.ts` proxies `/api` and `/ws` to `VITE_BACKEND_HTTP_URL` (default `http://127.0.0.1:8000`).
In dev mode, intent POSTs use `/api/intent` through this proxy, avoiding browser CORS issues when UI runs on port `5173`.

## Build

```bash
npm run build
npm run preview
```

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
