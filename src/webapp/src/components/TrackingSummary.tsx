import { memo } from "react";
import { OverlaySource, TrackingState, formatNumber, toTrackingStateName } from "../types";
import { clampNumber, formatSeconds } from "../utils/format";
import AimIndicator from "./AimIndicator";
import Sparkline from "./Sparkline";
import StatusBadge, { BadgeSeverity } from "./StatusBadge";

interface TrackingSummaryProps {
  overlaySource: OverlaySource;
  trackingState: number | null;
  trackingBlockedReason: string | null;
  confidence: number | null;
  boundW: number | null;
  boundH: number | null;
  targetX: number | null;
  targetY: number | null;
  ageS: number | null;
  freshThresholdS: number;
  confidenceHistory: readonly number[];
  ageHistory: readonly number[];
}

function trackingStateSeverity(trackingState: number | null): BadgeSeverity {
  switch (trackingState) {
    case TrackingState.TargetDetected:
      return "info";
    case TrackingState.Tracking:
      return "good";
    case TrackingState.Searching:
      return "warn";
    case TrackingState.NoTarget:
      return "neutral";
    default:
      return "neutral";
  }
}

function blockedReasonToBadge(reason: string | null): { label: string; severity: BadgeSeverity } | null {
  if (!reason) {
    return null;
  }

  if (reason === "stale_vis") {
    return { label: "Vision stale", severity: "warn" };
  }

  if (reason === "no_vis") {
    return { label: "No vision", severity: "bad" };
  }

  return { label: `Blocked: ${reason}`, severity: "warn" };
}

function TrackingSummary({
  overlaySource,
  trackingState,
  trackingBlockedReason,
  confidence,
  boundW,
  boundH,
  targetX,
  targetY,
  ageS,
  freshThresholdS,
  confidenceHistory,
  ageHistory,
}: TrackingSummaryProps): JSX.Element {
  const stateName = toTrackingStateName(trackingState);
  const confidenceValue = confidence === null ? 0 : clampNumber(confidence, 0, 1);
  const confidencePct = Math.round(confidenceValue * 100);
  const isLowConfidence = confidence !== null && confidence < 0.5;
  const ageThreshold = freshThresholdS > 0 ? freshThresholdS : overlaySource === "VIS" ? 0.25 : 0.5;
  const isStale = ageS !== null && ageS >= ageThreshold;
  const blockedBadge = blockedReasonToBadge(trackingBlockedReason);

  return (
    <section className="panel tracking-summary-panel">
      <div className="tracking-summary-panel__header">
        <h2>Tracking Summary</h2>
        <StatusBadge label={`Source ${overlaySource}`} severity="info" />
      </div>

      <div className="tracking-summary-panel__row">
        <span className="tracking-summary-panel__label">State</span>
        <StatusBadge label={stateName} severity={trackingStateSeverity(trackingState)} />
      </div>

      {blockedBadge ? (
        <div className="tracking-summary-panel__row">
          <span className="tracking-summary-panel__label">Blocked</span>
          <StatusBadge label={blockedBadge.label} severity={blockedBadge.severity} />
        </div>
      ) : null}

      <div className="tracking-summary-panel__row">
        <span className="tracking-summary-panel__label">Confidence</span>
        <span className="tracking-summary-panel__value">{formatNumber(confidence, 2)}</span>
      </div>

      <div className="tracking-summary-panel__confidence-track" role="img" aria-label="Confidence bar">
        <div
          className={`tracking-summary-panel__confidence-fill${isLowConfidence ? " is-low" : ""}`}
          style={{ width: `${confidencePct}%` }}
        />
      </div>

      <div className="tracking-summary-panel__row">
        <span className="tracking-summary-panel__label">BBox (w/h)</span>
        <span className="tracking-summary-panel__value">
          {formatNumber(boundW ?? 0, 3)} / {formatNumber(boundH ?? 0, 3)}
        </span>
      </div>

      <div className="tracking-summary-panel__row">
        <span className="tracking-summary-panel__label">Age</span>
        <StatusBadge
          label={formatSeconds(ageS, 2)}
          severity={ageS === null ? "neutral" : isStale ? "warn" : "good"}
          subtext={`fresh < ${formatSeconds(ageThreshold, 2)}`}
        />
      </div>

      <div className="tracking-summary-panel__aim">
        <div>
          <p className="tracking-summary-panel__label">Target offset</p>
          <p className="tracking-summary-panel__value">
            x {formatNumber(targetX, 3)} / y {formatNumber(targetY, 3)}
          </p>
        </div>
        <AimIndicator x={targetX} y={targetY} />
      </div>

      <div className="tracking-summary-panel__charts">
        <div>
          <p className="tracking-summary-panel__chart-title">Confidence history</p>
          <Sparkline
            values={confidenceHistory}
            min={0}
            max={1}
            label="Confidence history sparkline"
            color="#2db878"
          />
        </div>

        <div>
          <p className="tracking-summary-panel__chart-title">Age history</p>
          <Sparkline
            values={ageHistory}
            min={0}
            max={Math.max(0.05, ageThreshold * 4)}
            label="Age history sparkline"
            color="#d88a2f"
          />
        </div>
      </div>
    </section>
  );
}

export default memo(TrackingSummary);
