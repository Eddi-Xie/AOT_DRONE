import { ReactNode } from "react";

export interface StatusRow {
  label: string;
  value: ReactNode;
}

interface StatusCardProps {
  title: string;
  subtitle?: string;
  rows: StatusRow[];
  accent?: "neutral" | "ok" | "warn" | "error";
}

export default function StatusCard({
  title,
  subtitle,
  rows,
  accent = "neutral",
}: StatusCardProps): JSX.Element {
  return (
    <section className={`status-card status-card--${accent}`}>
      <header className="status-card__header">
        <h3>{title}</h3>
        {subtitle ? <p>{subtitle}</p> : null}
      </header>
      <dl className="status-card__rows">
        {rows.length === 0 ? (
          <div className="status-card__empty">No data yet</div>
        ) : (
          rows.map((row) => (
            <div className="status-card__row" key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))
        )}
      </dl>
    </section>
  );
}
