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

// Find the Vision-Link status-card and return its accent class
// (warn / neutral / ok / error). Asserts there is exactly ONE Vision-Link
// card in the rendered output so a future duplicate cannot silently ride
// through under the wrong assertion.
function visAccent(html: string): string {
  const cards = html.split('<section class="status-card');
  const visionCards = cards.filter((c) => c.includes("<h3>Vision Link</h3>"));
  if (visionCards.length !== 1) {
    throw new Error(
      `expected exactly 1 Vision Link status-card, found ${visionCards.length}`,
    );
  }
  const match = / status-card--(\w+)">/.exec(visionCards[0]);
  if (match === null) {
    throw new Error("Vision Link card found but no accent class extracted");
  }
  return match[1];
}

describe("TelemetryPanel vision accent uses projected age + App-resolved threshold", () => {
  it("stays neutral when projectedVisAgeS < visFreshThresholdS", () => {
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_fresh_s: 0.25 })}
        latestTel={null}
        latestVis={null}
        nowMs={1_000_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
        projectedVisAgeS={0.1}
        visFreshThresholdS={0.25}
      />,
    );
    expect(visAccent(html)).toBe("neutral");
  });

  it("flips to warn when projectedVisAgeS > visFreshThresholdS", () => {
    // The bug case from the original review: raw linkStatus.vis_age_s is
    // 0.1 (would have stayed neutral on the legacy comparison), but the
    // App-projected value is 1.1 s after 1 s of elapsed time, which
    // exceeds the 0.25 s threshold and must accent warn.
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_age_s: 0.1, vis_fresh_s: 0.25 })}
        latestTel={null}
        latestVis={null}
        nowMs={1_001_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
        projectedVisAgeS={1.1}
        visFreshThresholdS={0.25}
      />,
    );
    expect(visAccent(html)).toBe("warn");
  });

  it("stays neutral when projectedVisAgeS is null (no vis data yet)", () => {
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_age_s: null })}
        latestTel={null}
        latestVis={null}
        nowMs={1_001_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
        projectedVisAgeS={null}
        visFreshThresholdS={0.25}
      />,
    );
    expect(visAccent(html)).toBe("neutral");
  });

  it("uses the App-resolved threshold (default substituted upstream when wire value is 0)", () => {
    // App.tsx substitutes DEFAULT_VIS_FRESH_S = 0.25 when vis_fresh_s <= 0.
    // The panel must agree: an age of 0.1 with the resolved 0.25 threshold
    // stays neutral, regardless of the raw linkStatus.vis_fresh_s value.
    const html = renderToString(
      <TelemetryPanel
        linkStatus={makeLinkStatus({ vis_fresh_s: 0 })}
        latestTel={null}
        latestVis={null}
        nowMs={1_000_000}
        linkUpdatedAtMs={1_000_000}
        linkEnvelopeTimestampS={null}
        projectedVisAgeS={0.1}
        visFreshThresholdS={0.25}
      />,
    );
    expect(visAccent(html)).toBe("neutral");
  });
});
