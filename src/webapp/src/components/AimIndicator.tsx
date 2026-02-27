import { memo } from "react";
import { clampNumber } from "../utils/format";

interface AimIndicatorProps {
  x: number | null;
  y: number | null;
}

function AimIndicator({ x, y }: AimIndicatorProps): JSX.Element {
  const hasTarget = x !== null && y !== null;
  const xClamped = hasTarget ? clampNumber(x, -1, 1) : 0;
  const yClamped = hasTarget ? clampNumber(y, -1, 1) : 0;
  const dotX = ((xClamped + 1) / 2) * 100;
  const dotY = (1 - (yClamped + 1) / 2) * 100;

  return (
    <svg aria-label="Target offset indicator" className="aim-indicator" viewBox="0 0 100 100">
      <rect className="aim-indicator__frame" x="1" y="1" width="98" height="98" rx="10" />
      <line className="aim-indicator__axis" x1="0" y1="50" x2="100" y2="50" />
      <line className="aim-indicator__axis" x1="50" y1="0" x2="50" y2="100" />
      {hasTarget ? <circle className="aim-indicator__dot" cx={dotX} cy={dotY} r="5" /> : null}
    </svg>
  );
}

export default memo(AimIndicator);
