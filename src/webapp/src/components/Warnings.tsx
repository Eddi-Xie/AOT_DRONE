import { memo, useMemo } from "react";
import { StatusAlert, WarningEntry, WarningSeverity } from "../types";
import StatusBadge from "./StatusBadge";

interface WarningsProps {
  derivedAlerts: StatusAlert[];
  warnings: WarningEntry[];
  onClear: () => void;
}

interface GroupedWarning {
  kind: string;
  detail: string;
  severity: WarningSeverity;
  count: number;
  lastReceivedAtMs: number;
}

function severityLabel(severity: WarningSeverity): string {
  if (severity === "error") {
    return "Error";
  }
  if (severity === "warn") {
    return "Warn";
  }
  return "Info";
}

function severityToBadge(severity: WarningSeverity): "info" | "warn" | "bad" {
  if (severity === "error") {
    return "bad";
  }
  if (severity === "warn") {
    return "warn";
  }
  return "info";
}

function Warnings({ derivedAlerts, warnings, onClear }: WarningsProps): JSX.Element {
  const groupedWarnings = useMemo(() => {
    const byKind = new Map<string, GroupedWarning>();

    for (const warning of warnings) {
      const existing = byKind.get(warning.kind);

      if (existing) {
        existing.count += 1;
        if (warning.receivedAtMs > existing.lastReceivedAtMs) {
          existing.detail = warning.detail;
          existing.severity = warning.severity;
          existing.lastReceivedAtMs = warning.receivedAtMs;
        }
      } else {
        byKind.set(warning.kind, {
          kind: warning.kind,
          detail: warning.detail,
          severity: warning.severity,
          count: 1,
          lastReceivedAtMs: warning.receivedAtMs,
        });
      }
    }

    return Array.from(byKind.values())
      .sort((a, b) => b.lastReceivedAtMs - a.lastReceivedAtMs)
      .slice(0, 5);
  }, [warnings]);

  return (
    <section className="panel warnings-panel">
      <div className="warnings-panel__header">
        <h2>Warnings</h2>
        <button className="warnings-panel__clear" onClick={onClear} type="button">
          Clear
        </button>
      </div>

      {derivedAlerts.length > 0 ? (
        <div className="warnings-panel__status-list">
          {derivedAlerts.map((alert) => (
            <div className={`warning-item warning-item--${alert.severity}`} key={alert.id}>
              <StatusBadge
                label={severityLabel(alert.severity)}
                severity={severityToBadge(alert.severity)}
              />
              <p>{alert.detail}</p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="warnings-panel__event-list">
        {groupedWarnings.length === 0 ? (
          <p className="warnings-panel__empty">No warning events received.</p>
        ) : (
          groupedWarnings.map((warning) => (
            <div className={`warning-item warning-item--${warning.severity}`} key={warning.kind}>
              <StatusBadge
                label={severityLabel(warning.severity)}
                severity={severityToBadge(warning.severity)}
              />
              <div>
                <p className="warning-item__kind">
                  {warning.kind} x{warning.count}
                </p>
                <p>{warning.detail}</p>
                <p className="warning-item__time">
                  Last seen {new Date(warning.lastReceivedAtMs).toLocaleTimeString()}
                </p>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

export default memo(Warnings);
