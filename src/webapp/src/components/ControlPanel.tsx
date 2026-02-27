import { memo } from "react";
import { ControlMode, toControlModeName } from "../types";

interface ControlPanelProps {
  selectedMode: ControlMode;
  actualMode: number | null;
  armed: boolean;
  pending: boolean;
  trackingBlockedReason: string | null;
  feedbackKind: "idle" | "sending" | "success" | "error";
  feedbackMessage: string;
  onSelectMode: (mode: ControlMode) => void;
  onToggleArm: () => void;
}

const MODE_OPTIONS: Array<{ mode: ControlMode; label: string }> = [
  { mode: ControlMode.Manual, label: "Manual" },
  { mode: ControlMode.Tracking, label: "Tracking" },
  { mode: ControlMode.Takeoff, label: "Takeoff" },
  { mode: ControlMode.LandSafely, label: "LandSafely" },
];

function ControlPanel({
  selectedMode,
  actualMode,
  armed,
  pending,
  trackingBlockedReason,
  feedbackKind,
  feedbackMessage,
  onSelectMode,
  onToggleArm,
}: ControlPanelProps): JSX.Element {
  return (
    <section className="panel controls-panel">
      <h2>Controls</h2>
      <p className="controls-panel__meta">Actual mode: {toControlModeName(actualMode)}</p>

      <div className="mode-grid">
        {MODE_OPTIONS.map((option) => {
          const trackingDisabled =
            option.mode === ControlMode.Tracking && trackingBlockedReason !== null;

          return (
            <button
              key={option.mode}
              className={`mode-button ${selectedMode === option.mode ? "is-selected" : ""}`}
              disabled={pending || trackingDisabled}
              onClick={() => {
                onSelectMode(option.mode);
              }}
              type="button"
            >
              {option.label}
            </button>
          );
        })}
      </div>

      <button
        className={`arm-button ${armed ? "is-armed" : "is-disarmed"}`}
        disabled={pending}
        onClick={onToggleArm}
        type="button"
      >
        {armed ? "Disarm" : "Arm"}
      </button>

      {trackingBlockedReason ? (
        <p className="controls-panel__warning">Tracking blocked: {trackingBlockedReason}</p>
      ) : null}

      <p className={`controls-panel__feedback controls-panel__feedback--${feedbackKind}`}>
        {feedbackMessage}
      </p>
    </section>
  );
}

export default memo(ControlPanel);
