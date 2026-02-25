import { StatusAlert, WarningEntry } from "../types";

interface WarningsProps {
  statusAlerts: StatusAlert[];
  warnings: WarningEntry[];
}

function severityLabel(severity: "info" | "warn" | "error"): string {
  if (severity === "error") {
    return "Error";
  }
  if (severity === "warn") {
    return "Warn";
  }
  return "Info";
}

export default function Warnings({ statusAlerts, warnings }: WarningsProps): JSX.Element {
  const recentWarnings = warnings.slice(0, 3);

  return (
    <section className="panel warnings-panel">
      <h2>Warnings</h2>

      {statusAlerts.length > 0 ? (
        <div className="warnings-panel__status-list">
          {statusAlerts.map((alert) => (
            <div className={`warning-item warning-item--${alert.severity}`} key={alert.id}>
              <span className="warning-item__badge">{severityLabel(alert.severity)}</span>
              <p>{alert.detail}</p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="warnings-panel__event-list">
        {recentWarnings.length === 0 ? (
          <p className="warnings-panel__empty">No warning events received.</p>
        ) : (
          recentWarnings.map((warning) => (
            <div
              className={`warning-item warning-item--${warning.severity}`}
              key={`${warning.envelopeSeq}-${warning.kind}`}
            >
              <span className="warning-item__badge">{severityLabel(warning.severity)}</span>
              <div>
                <p className="warning-item__kind">{warning.kind}</p>
                <p>{warning.detail}</p>
                <p className="warning-item__time">
                  {new Date(warning.receivedAtMs).toLocaleTimeString()}
                </p>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
