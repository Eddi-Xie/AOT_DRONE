import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TelemetryPanel from "./TelemetryPanel";
import { LinkStatus } from "../types";

function makeLinkStatus(overrides: Partial<LinkStatus> = {}): LinkStatus {
  return {
    fc_connected: true,
    fc_last_connect_attempt_s: null,
    cmd_tx_total: 0,
    cmd_tx_ok: 0,
    cmd_tx_fail: 0,
    cmd_last_sent_monotonic_s: null,
    cmd_hz_est: 50,
    tracking_blocked_reason: null,
    vis_age_s: 0.1,
    vis_rx_ok: 0,
    vis_rx_bad: 0,
    vis_drop_reason_oversize: 0,
    vis_drop_reason_json: 0,
    vis_drop_reason_schema: 0,
    vis_drop_reason_range: 0,
    vis_drop_reason_semantics: 0,
    tel_age_s: 0.1,
    tel_rx_ok: 0,
    tel_rx_bad: 0,
    vis_fresh_s: 0.25,
    tel_fresh_s: 0.5,
    cmd_timeout_s: 0.5,
    cmd_hz: 50,
    tel_hz: 50,
    ...overrides,
  };
}

// Find the Vision-Link status-card and return its accent class (warn/neutral).
// We split on `<section class="status-card` so we get one entry per card and
// can pick the one that holds the "Vision Link" h3 — avoids cross-card matches.
function visAccent(html: string): string | null {
  const cards = html.split('<section class="status-card');
  const visionCard = cards.find((c) => c.includes("<h3>Vision Link</h3>"));
  if (!visionCard) return null;
  const re = / status-card--(\w+)">/;
  const match = re.exec(visionCard);
  return match ? match[1] : null;
}

describe("TelemetryPanel vision accent uses projected age", () => {
  it("stays neutral when linkUpdatedAtMs == nowMs and snapshot age < fresh threshold", () => {
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_age_s: 0.1, vis_fresh_s: 0.25 })}
        latestTel={null}
        latestVis={null}
        nowMs={1_000_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
      />,
    );
    expect(visAccent(html)).toBe("neutral");
  });

  it("flips to warn when the projected age (snapshot + elapsed) exceeds the threshold", () => {
    // snapshot vis_age_s = 0.1; elapsed = (1_001_000 - 1_000_000) / 1000 = 1 s.
    // projected = 1.1 s > vis_fresh_s = 0.25 s -> accent should warn even
    // though the raw vis_age_s field still reads 0.1.
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_age_s: 0.1, vis_fresh_s: 0.25 })}
        latestTel={null}
        latestVis={null}
        nowMs={1_001_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
      />,
    );
    expect(visAccent(html)).toBe("warn");
  });

  it("stays neutral if the snapshot is null (no vis data yet, not a stale signal)", () => {
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_age_s: null })}
        latestTel={null}
        latestVis={null}
        nowMs={1_001_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
      />,
    );
    expect(visAccent(html)).toBe("neutral");
  });
});
