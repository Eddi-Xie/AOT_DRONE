import { useEffect, useMemo, useRef, useState } from "react";
import {
  OverlaySource,
  TelUpdate,
  TrackingState,
  VisUpdate,
  formatNumber,
  readNumber,
  toTrackingStateName,
} from "../types";
import { NormalizedBox, normalizedToCanvasRect } from "../utils/overlayMath";

interface VideoPanelProps {
  videoUrl: string;
  overlaySource: OverlaySource;
  latestVis: VisUpdate | null;
  latestTel: TelUpdate | null;
}

interface OverlaySample {
  box: NormalizedBox;
  trackingState: number | null;
  confidence: number | null;
}

function getOverlaySample(
  overlaySource: OverlaySource,
  latestVis: VisUpdate | null,
  latestTel: TelUpdate | null,
): OverlaySample | null {
  if (overlaySource === "TEL") {
    if (latestTel === null) {
      return null;
    }

    const centerX = readNumber(latestTel.target_x);
    const centerY = readNumber(latestTel.target_y);
    const boundW = readNumber(latestTel.bound_w);
    const boundH = readNumber(latestTel.bound_h);

    if (centerX === null || centerY === null || boundW === null || boundH === null) {
      return null;
    }

    return {
      box: {
        centerX,
        centerY,
        boundW,
        boundH,
      },
      trackingState: readNumber(latestTel.tracking_state),
      confidence: readNumber(latestTel.confidence),
    };
  }

  if (latestVis === null) {
    return null;
  }

  const centerX = readNumber(latestVis.loc_x);
  const centerY = readNumber(latestVis.loc_y);
  const boundW = readNumber(latestVis.bound_w);
  const boundH = readNumber(latestVis.bound_h);

  if (centerX === null || centerY === null || boundW === null || boundH === null) {
    return null;
  }

  return {
    box: {
      centerX,
      centerY,
      boundW,
      boundH,
    },
    trackingState: readNumber(latestVis.tracking_state),
    confidence: readNumber(latestVis.confidence),
  };
}

export default function VideoPanel({
  videoUrl,
  overlaySource,
  latestVis,
  latestTel,
}: VideoPanelProps): JSX.Element {
  const isDev = import.meta.env.DEV;
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [streamLoaded, setStreamLoaded] = useState(false);
  const [streamError, setStreamError] = useState(false);

  const isMockMode = useMemo(() => {
    const normalized = videoUrl.trim().toLowerCase();
    return normalized.length === 0 || normalized === "mock" || normalized === "none";
  }, [videoUrl]);

  const overlaySample = useMemo(
    () => getOverlaySample(overlaySource, latestVis, latestTel),
    [overlaySource, latestVis, latestTel],
  );

  const trackingStateName = overlaySample
    ? toTrackingStateName(overlaySample.trackingState)
    : "No data yet";
  const confidenceLabel = overlaySample ? formatNumber(overlaySample.confidence, 2) : "n/a";
  const sourceLabel = overlaySource === "TEL" ? "TEL_UPDATE" : "VIS_UPDATE";

  useEffect(() => {
    setStreamLoaded(false);
    setStreamError(false);
  }, [videoUrl]);

  useEffect(() => {
    const stage = stageRef.current;
    if (stage === null) {
      return;
    }

    const updateSize = () => {
      const nextWidth = Math.max(0, Math.floor(stage.clientWidth));
      const nextHeight = Math.max(0, Math.floor(stage.clientHeight));
      setCanvasSize((prev) => {
        if (prev.width === nextWidth && prev.height === nextHeight) {
          return prev;
        }
        return { width: nextWidth, height: nextHeight };
      });
    };

    updateSize();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateSize);
      return () => {
        window.removeEventListener("resize", updateSize);
      };
    }

    const observer = new ResizeObserver(() => {
      updateSize();
    });
    observer.observe(stage);

    return () => {
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) {
      return;
    }

    const width = canvasSize.width;
    const height = canvasSize.height;
    if (width <= 0 || height <= 0) {
      return;
    }

    const dpr = window.devicePixelRatio || 1;
    const pixelWidth = Math.max(1, Math.round(width * dpr));
    const pixelHeight = Math.max(1, Math.round(height * dpr));

    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }

    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext("2d");
    if (ctx === null) {
      return;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    if (overlaySample === null) {
      return;
    }

    const rect = normalizedToCanvasRect(overlaySample.box, width, height);
    if (rect === null) {
      return;
    }

    const isTracking = overlaySample.trackingState === TrackingState.Tracking;

    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.55)";
    ctx.beginPath();
    ctx.moveTo(rect.centerX - 8, rect.centerY);
    ctx.lineTo(rect.centerX + 8, rect.centerY);
    ctx.moveTo(rect.centerX, rect.centerY - 8);
    ctx.lineTo(rect.centerX, rect.centerY + 8);
    ctx.stroke();

    if (isTracking && rect.width > 0 && rect.height > 0) {
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#19c27e";
      ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);

      const label = `${trackingStateName} ${formatNumber(overlaySample.confidence, 2)}`;
      ctx.font = "12px Space Grotesk, sans-serif";
      const textWidth = Math.ceil(ctx.measureText(label).width);
      const labelX = Math.max(0, Math.min(width - (textWidth + 14), rect.x));
      const labelY = Math.max(16, rect.y - 6);

      ctx.fillStyle = "rgba(12, 35, 55, 0.8)";
      ctx.fillRect(labelX, labelY - 14, textWidth + 10, 16);
      ctx.fillStyle = "#f4fcff";
      ctx.fillText(label, labelX + 5, labelY - 2);
    }
  }, [canvasSize, overlaySample, trackingStateName]);

  return (
    <section className="panel video-panel">
      <div className="video-panel__header">
        <h2>Video</h2>
        <p>Overlay source: {overlaySource}</p>
      </div>

      <div className="video-stage" ref={stageRef}>
        {!isMockMode && !streamError ? (
          <img
            alt="Live video stream"
            className="video-stage__image"
            onError={() => {
              setStreamError(true);
            }}
            onLoad={() => {
              setStreamLoaded(true);
            }}
            src={videoUrl}
          />
        ) : (
          <div className="video-stage__placeholder">No video stream</div>
        )}

        <canvas className="video-stage__overlay" ref={canvasRef} />

        {!isMockMode && !streamLoaded && !streamError ? (
          <p className="video-stage__hint">Connecting to stream...</p>
        ) : null}

        {overlaySample === null ? (
          <p className="video-stage__hint video-stage__hint--left">No data yet</p>
        ) : null}
      </div>

      <div className="video-panel__meta">
        <span>Source: {sourceLabel}</span>
        <span>State: {trackingStateName}</span>
        <span>Confidence: {confidenceLabel}</span>
      </div>

      {isDev && overlaySample !== null ? (
        <p className="video-panel__debug">
          raw: x={formatNumber(overlaySample.box.centerX, 3)} y=
          {formatNumber(overlaySample.box.centerY, 3)} w=
          {formatNumber(overlaySample.box.boundW, 3)} h=
          {formatNumber(overlaySample.box.boundH, 3)}
        </p>
      ) : null}
    </section>
  );
}
