import { memo, useEffect, useMemo, useRef, useState } from "react";
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

interface ComparableOverlayFields {
  centerX: number | null;
  centerY: number | null;
  boundW: number | null;
  boundH: number | null;
  trackingState: number | null;
  confidence: number | null;
}

function readVisComparableFields(latestVis: VisUpdate | null): ComparableOverlayFields {
  return {
    centerX: readNumber(latestVis?.loc_x),
    centerY: readNumber(latestVis?.loc_y),
    boundW: readNumber(latestVis?.bound_w),
    boundH: readNumber(latestVis?.bound_h),
    trackingState: readNumber(latestVis?.tracking_state),
    confidence: readNumber(latestVis?.confidence),
  };
}

function readTelComparableFields(latestTel: TelUpdate | null): ComparableOverlayFields {
  return {
    centerX: readNumber(latestTel?.target_x),
    centerY: readNumber(latestTel?.target_y),
    boundW: readNumber(latestTel?.bound_w),
    boundH: readNumber(latestTel?.bound_h),
    trackingState: readNumber(latestTel?.tracking_state),
    confidence: readNumber(latestTel?.confidence),
  };
}

function equalNullableNumber(left: number | null, right: number | null): boolean {
  return left === right;
}

function equalComparableFields(
  left: ComparableOverlayFields,
  right: ComparableOverlayFields,
): boolean {
  return (
    equalNullableNumber(left.centerX, right.centerX) &&
    equalNullableNumber(left.centerY, right.centerY) &&
    equalNullableNumber(left.boundW, right.boundW) &&
    equalNullableNumber(left.boundH, right.boundH) &&
    equalNullableNumber(left.trackingState, right.trackingState) &&
    equalNullableNumber(left.confidence, right.confidence)
  );
}

function areVideoPanelPropsEqual(previous: VideoPanelProps, next: VideoPanelProps): boolean {
  if (previous.videoUrl !== next.videoUrl || previous.overlaySource !== next.overlaySource) {
    return false;
  }

  if (next.overlaySource === "VIS") {
    return equalComparableFields(
      readVisComparableFields(previous.latestVis),
      readVisComparableFields(next.latestVis),
    );
  }

  return equalComparableFields(
    readTelComparableFields(previous.latestTel),
    readTelComparableFields(next.latestTel),
  );
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

function drawCenteredLabel(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  text: string,
): void {
  ctx.font = "700 16px Space Grotesk, sans-serif";
  const textWidth = Math.ceil(ctx.measureText(text).width);
  const padX = 12;
  const padY = 8;
  const boxWidth = textWidth + padX * 2;
  const boxHeight = 32;
  const x = (width - boxWidth) / 2;
  const y = (height - boxHeight) / 2;

  ctx.fillStyle = "rgba(5, 21, 35, 0.74)";
  ctx.strokeStyle = "rgba(155, 194, 219, 0.65)";
  ctx.lineWidth = 1;
  ctx.fillRect(x, y, boxWidth, boxHeight);
  ctx.strokeRect(x, y, boxWidth, boxHeight);

  ctx.fillStyle = "#e7f7ff";
  ctx.fillText(text, x + padX, y + boxHeight - padY);
}

function VideoPanel({
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

    const stateName = toTrackingStateName(overlaySample.trackingState).toUpperCase();
    const isTracking = overlaySample.trackingState === TrackingState.Tracking;

    if (!isTracking) {
      drawCenteredLabel(ctx, width, height, stateName);
      return;
    }

    const rect = normalizedToCanvasRect(overlaySample.box, width, height);
    if (rect === null || rect.width <= 0 || rect.height <= 0) {
      drawCenteredLabel(ctx, width, height, "TRACKING");
      return;
    }

    const lowConfidence = overlaySample.confidence !== null && overlaySample.confidence < 0.5;
    const stroke = lowConfidence ? "#e0a24a" : "#19c27e";

    ctx.lineWidth = 1.4;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.42)";
    ctx.beginPath();
    ctx.moveTo(rect.centerX - 8, rect.centerY);
    ctx.lineTo(rect.centerX + 8, rect.centerY);
    ctx.moveTo(rect.centerX, rect.centerY - 8);
    ctx.lineTo(rect.centerX, rect.centerY + 8);
    ctx.stroke();

    ctx.lineWidth = 2;
    ctx.strokeStyle = stroke;
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);

    const lineA = `TRACKING ${overlaySource}`;
    const lineB = `conf ${formatNumber(overlaySample.confidence, 2)}${lowConfidence ? " LOW CONF" : ""}`;
    const lineC = `w ${formatNumber(overlaySample.box.boundW, 2)} h ${formatNumber(overlaySample.box.boundH, 2)}`;

    ctx.font = "12px Space Grotesk, sans-serif";
    const textWidth = Math.ceil(
      Math.max(ctx.measureText(lineA).width, ctx.measureText(lineB).width, ctx.measureText(lineC).width),
    );

    const labelX = Math.max(0, Math.min(width - (textWidth + 14), rect.x));
    const labelY = Math.max(42, rect.y - 8);
    const lineHeight = 14;

    ctx.fillStyle = "rgba(8, 27, 43, 0.84)";
    ctx.strokeStyle = "rgba(149, 184, 210, 0.7)";
    ctx.lineWidth = 1;
    ctx.fillRect(labelX, labelY - lineHeight * 3, textWidth + 10, lineHeight * 3 + 6);
    ctx.strokeRect(labelX, labelY - lineHeight * 3, textWidth + 10, lineHeight * 3 + 6);

    ctx.fillStyle = "#e8f9ff";
    ctx.fillText(lineA, labelX + 5, labelY - lineHeight * 2 + 2);
    ctx.fillText(lineB, labelX + 5, labelY - lineHeight + 2);
    ctx.fillText(lineC, labelX + 5, labelY + 2);
  }, [canvasSize, overlaySample, overlaySource]);

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

export default memo(VideoPanel, areVideoPanelPropsEqual);
