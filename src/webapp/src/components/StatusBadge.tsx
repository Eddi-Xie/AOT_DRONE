import { memo } from "react";

export type BadgeSeverity = "neutral" | "info" | "good" | "warn" | "bad";

interface StatusBadgeProps {
  label: string;
  severity: BadgeSeverity;
  subtext?: string;
  title?: string;
  className?: string;
}

function StatusBadge({ label, severity, subtext, title, className }: StatusBadgeProps): JSX.Element {
  return (
    <span
      className={`status-badge status-badge--${severity}${className ? ` ${className}` : ""}`}
      title={title}
    >
      <span className="status-badge__label">{label}</span>
      {subtext ? <span className="status-badge__subtext">{subtext}</span> : null}
    </span>
  );
}

export default memo(StatusBadge);
