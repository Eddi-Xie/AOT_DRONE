import { BackendConfig, IntentRequest, OverlaySource } from "./types";

const DEFAULT_HTTP_URL = "http://127.0.0.1:8000";
const DEFAULT_WS_URL = "ws://127.0.0.1:8000/ws";
const DEFAULT_VIDEO_URL = "/video";
const DEFAULT_OVERLAY_SOURCE: OverlaySource = "VIS";

function trimTrailingSlash(value: string): string {
  return value === "/" ? "/" : value.replace(/\/+$/, "");
}

function deriveWsUrl(httpUrl: string): string {
  try {
    const parsed = new URL(httpUrl, window.location.origin);
    parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
    parsed.pathname = "/ws";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return DEFAULT_WS_URL;
  }
}

function normalizeWsUrl(rawWsUrl: string): string {
  try {
    const parsed = new URL(rawWsUrl, window.location.origin);
    if (parsed.protocol === "http:") {
      parsed.protocol = "ws:";
    } else if (parsed.protocol === "https:") {
      parsed.protocol = "wss:";
    } else if (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") {
      return DEFAULT_WS_URL;
    }
    if (parsed.pathname === "/" || parsed.pathname === "") {
      parsed.pathname = "/ws";
    }
    return parsed.toString();
  } catch {
    return DEFAULT_WS_URL;
  }
}

function deriveDevWsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function normalizeOverlaySource(raw: string | undefined): OverlaySource {
  const normalized = raw?.trim().toUpperCase();
  if (normalized === "TEL") {
    return "TEL";
  }
  return DEFAULT_OVERLAY_SOURCE;
}

function buildUrl(baseUrl: string, path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  try {
    const parsed = new URL(baseUrl, window.location.origin);
    if (parsed.origin === window.location.origin && !baseUrl.startsWith("http")) {
      const basePath = trimTrailingSlash(parsed.pathname);
      return basePath === "/" ? normalizedPath : `${basePath}${normalizedPath}`;
    }
    parsed.pathname = normalizedPath;
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return `${trimTrailingSlash(baseUrl)}${normalizedPath}`;
  }
}

export function getBackendConfig(): BackendConfig {
  const envHttpUrl = import.meta.env.VITE_BACKEND_HTTP_URL as string | undefined;
  const envWsUrl = import.meta.env.VITE_BACKEND_WS_URL as string | undefined;
  const envVideoUrl = import.meta.env.VITE_VIDEO_URL as string | undefined;
  const envOverlaySource = import.meta.env.VITE_OVERLAY_SOURCE as string | undefined;
  const isDev = import.meta.env.DEV;

  const httpUrl = envHttpUrl && envHttpUrl.trim() ? envHttpUrl.trim() : DEFAULT_HTTP_URL;
  const videoUrl = envVideoUrl && envVideoUrl.trim() ? envVideoUrl.trim() : DEFAULT_VIDEO_URL;
  const wsUrl = isDev
    ? deriveDevWsUrl()
    : envWsUrl && envWsUrl.trim()
      ? normalizeWsUrl(envWsUrl.trim())
      : deriveWsUrl(httpUrl);
  const overlaySource = normalizeOverlaySource(envOverlaySource);

  return {
    httpUrl: trimTrailingSlash(httpUrl),
    wsUrl,
    videoUrl,
    overlaySource,
  };
}

export async function postIntent(httpBaseUrl: string, payload: IntentRequest): Promise<void> {
  // In Vite dev, use same-origin path so the dev proxy handles backend routing.
  const endpoint = import.meta.env.DEV ? "/api/intent" : buildUrl(httpBaseUrl, "/api/intent");

  let response: Response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : "Unknown network error";
    throw new Error(
      `POST ${endpoint} failed: ${detail}. Hint: Is backend running and is Vite proxy configured?`,
    );
  }

  if (response.ok) {
    return;
  }

  const errorText = await response.text();
  const detail = errorText.trim() || "No response body";
  throw new Error(
    `POST ${endpoint} failed (${response.status} ${response.statusText}): ${detail}. Hint: Verify backend health and proxy config.`,
  );
}
