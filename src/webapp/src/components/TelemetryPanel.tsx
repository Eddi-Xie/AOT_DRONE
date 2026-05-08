import StatusCard, { StatusRow } from "./StatusCard";
import {
  LinkStatus,
  TelUpdate,
  VisUpdate,
  formatNumber,
  readNumber,
  toControlModeName,
  toTrackingStateName,
} from "../types";
import { projectAge } from "../utils/format";

interface TelemetryPanelProps {
  linkStatus: LinkStatus | null;
  latestTel: TelUpdate | null;
  latestVis: VisUpdate | null;
  nowMs: number;
  linkUpdatedAtMs: number | null;
  linkEnvelopeTimestampS: number | null;
  // Pre-projected vis_age (snapshot age + elapsed since LINK_STATUS arrival),
  // hoisted from App so the value is computed once per render rather than
  // recomputed in three places (App-level alerts, this body, the
  // buildVisionRows helper).
  projectedVisAgeS: number | null;
}

function buildFcRows(
  linkStatus: LinkStatus | null,
  nowMs: number,
  linkUpdatedAtMs: number | null,
  linkEnvelopeTimestampS: number | null,
): StatusRow[] {
  if (linkStatus === null) {
    return [];
  }

  const cmdLastSentMonotonicS = readNumber(linkStatus.cmd_last_sent_monotonic_s);
  let cmdLastSentAgeS: number | null = null;

  if (cmdLastSentMonotonicS !== null && linkEnvelopeTimestampS !== null) {
    const baseAgeS = Math.max(0, linkEnvelopeTimestampS - cmdLastSentMonotonicS);
    cmdLastSentAgeS = projectAge(baseAgeS, linkUpdatedAtMs, nowMs);
  }

  return [
    {
      label: "fc_connected",
      value: linkStatus.fc_connected ? "Yes" : "No",
    },
    {
      label: "cmd_hz_est",
      value: formatNumber(readNumber(linkStatus.cmd_hz_est), 2, " Hz"),
    },
    {
      label: "cmd_tx_ok/fail",
      value: `${linkStatus.cmd_tx_ok}/${linkStatus.cmd_tx_fail}`,
    },
    {
      label: "cmd_last_sent_age",
      value: formatNumber(cmdLastSentAgeS, 2, " s"),
    },
  ];
}

function buildVisionRows(
  linkStatus: LinkStatus | null,
  visAgeS: number | null,
): StatusRow[] {
  if (linkStatus === null) {
    return [];
  }

  const drops = [
    `o:${linkStatus.vis_drop_reason_oversize}`,
    `j:${linkStatus.vis_drop_reason_json}`,
    `s:${linkStatus.vis_drop_reason_schema}`,
    `r:${linkStatus.vis_drop_reason_range}`,
    `sem:${linkStatus.vis_drop_reason_semantics}`,
  ].join(" ");

  return [
    {
      label: "vis_age",
      value: formatNumber(visAgeS, 2, " s"),
    },
    {
      label: "vis_rx_ok/bad",
      value: `${linkStatus.vis_rx_ok}/${linkStatus.vis_rx_bad}`,
    },
    {
      label: "vis_drop_summary",
      value: drops,
    },
  ];
}

function buildTelLinkRows(
  linkStatus: LinkStatus | null,
  nowMs: number,
  linkUpdatedAtMs: number | null,
): StatusRow[] {
  if (linkStatus === null) {
    return [];
  }

  const telAgeS = projectAge(readNumber(linkStatus.tel_age_s), linkUpdatedAtMs, nowMs);

  return [
    {
      label: "tel_age",
      value: formatNumber(telAgeS, 2, " s"),
    },
    {
      label: "tel_rx_ok/bad",
      value: `${linkStatus.tel_rx_ok}/${linkStatus.tel_rx_bad}`,
    },
  ];
}

function buildTelRows(latestTel: TelUpdate | null): StatusRow[] {
  if (latestTel === null) {
    return [];
  }

  const rows: StatusRow[] = [
    {
      label: "control_mode",
      value: toControlModeName(readNumber(latestTel.control_mode)),
    },
    {
      label: "tracking_state",
      value: toTrackingStateName(readNumber(latestTel.tracking_state)),
    },
  ];

  const cmdAgeS = readNumber(latestTel.cmd_age_s);
  if (cmdAgeS !== null) {
    rows.push({
      label: "cmd_age_s",
      value: formatNumber(cmdAgeS, 2, " s"),
    });
  }

  const distBottomM = readNumber(latestTel.distBottom_m);
  if (distBottomM !== null) {
    rows.push({
      label: "distBottom_m",
      value: formatNumber(distBottomM, 2, " m"),
    });
  }

  const distFrontM = readNumber(latestTel.distFront_m);
  if (distFrontM !== null) {
    rows.push({
      label: "distFront_m",
      value: formatNumber(distFrontM, 2, " m"),
    });
  }

  const distBackM = readNumber(latestTel.distBack_m);
  if (distBackM !== null) {
    rows.push({
      label: "distBack_m",
      value: formatNumber(distBackM, 2, " m"),
    });
  }

  rows.push({
    label: "target_x/target_y",
    value: `${formatNumber(readNumber(latestTel.target_x), 3)} / ${formatNumber(
      readNumber(latestTel.target_y),
      3,
    )}`,
  });

  rows.push({
    label: "bound_w/bound_h",
    value: `${formatNumber(readNumber(latestTel.bound_w), 3)} / ${formatNumber(
      readNumber(latestTel.bound_h),
      3,
    )}`,
  });

  rows.push({
    label: "confidence",
    value: formatNumber(readNumber(latestTel.confidence), 3),
  });

  return rows;
}

function buildVisRows(latestVis: VisUpdate | null): StatusRow[] {
  if (latestVis === null) {
    return [];
  }

  return [
    {
      label: "tracking_state",
      value: toTrackingStateName(readNumber(latestVis.tracking_state)),
    },
    {
      label: "loc_x/loc_y",
      value: `${formatNumber(readNumber(latestVis.loc_x), 3)} / ${formatNumber(
        readNumber(latestVis.loc_y),
        3,
      )}`,
    },
    {
      label: "bound_w/bound_h",
      value: `${formatNumber(readNumber(latestVis.bound_w), 3)} / ${formatNumber(
        readNumber(latestVis.bound_h),
        3,
      )}`,
    },
    {
      label: "confidence",
      value: formatNumber(readNumber(latestVis.confidence), 3),
    },
    {
      label: "vis_age_s",
      value: formatNumber(readNumber(latestVis.vis_age_s), 2, " s"),
    },
  ];
}

export default function TelemetryPanel({
  linkStatus,
  latestTel,
  latestVis,
  nowMs,
  linkUpdatedAtMs,
  linkEnvelopeTimestampS,
  projectedVisAgeS,
}: TelemetryPanelProps): JSX.Element {
  // Accent uses the App-projected vis_age so it tracks current freshness
  // (rather than the snapshot value at LINK_STATUS arrival, which goes stale
  // between frames and would let the accent stay green after the stream
  // died).
  const visFreshThresholdS = linkStatus ? readNumber(linkStatus.vis_fresh_s) : null;
  const visAccent =
    projectedVisAgeS !== null &&
    visFreshThresholdS !== null &&
    projectedVisAgeS > visFreshThresholdS
      ? "warn"
      : "neutral";

  return (
    <section className="panel telemetry-panel">
      <h2>Status &amp; Telemetry</h2>

      <div className="card-grid">
        <StatusCard
          title="FC Command Link"
          rows={buildFcRows(linkStatus, nowMs, linkUpdatedAtMs, linkEnvelopeTimestampS)}
          accent={linkStatus?.fc_connected ? "ok" : "warn"}
        />

        <StatusCard
          title="Vision Link"
          rows={buildVisionRows(linkStatus, projectedVisAgeS)}
          accent={visAccent}
        />

        <StatusCard
          title="Telemetry Link"
          rows={buildTelLinkRows(linkStatus, nowMs, linkUpdatedAtMs)}
          accent="neutral"
        />

        <StatusCard title="Latest TEL_UPDATE" rows={buildTelRows(latestTel)} accent="neutral" />

        <StatusCard title="Latest VIS_UPDATE" rows={buildVisRows(latestVis)} accent="neutral" />
      </div>
    </section>
  );
}
