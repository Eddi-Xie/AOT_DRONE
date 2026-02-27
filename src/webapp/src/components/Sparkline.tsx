import { memo, useMemo } from "react";
import { clampNumber } from "../utils/format";

interface SparklineProps {
  values: readonly number[];
  min: number;
  max: number;
  label: string;
  color?: string;
  className?: string;
}

function Sparkline({ values, min, max, label, color = "#2d9fcb", className }: SparklineProps): JSX.Element {
  const width = 220;
  const height = 52;
  const points = useMemo(() => {
    if (values.length === 0) {
      return "";
    }

    const valueSpan = Math.max(1e-6, max - min);
    return values
      .map((value, index) => {
        const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
        const normalized = (clampNumber(value, min, max) - min) / valueSpan;
        const y = height - normalized * height;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  }, [height, max, min, values, width]);

  return (
    <svg
      aria-label={label}
      className={`sparkline${className ? ` ${className}` : ""}`}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      <polyline className="sparkline__baseline" points={`0,${height} ${width},${height}`} />
      {points ? <polyline className="sparkline__line" points={points} style={{ stroke: color }} /> : null}
    </svg>
  );
}

export default memo(Sparkline);
