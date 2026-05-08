import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TrackingSummary from "./TrackingSummary";

interface RenderArgs {
  ageHistory: number[];
  freshThresholdS: number;
  overlaySource?: "VIS" | "TEL";
}

function render({ ageHistory, freshThresholdS, overlaySource = "VIS" }: RenderArgs): string {
  return renderToString(
    <TrackingSummary
      overlaySource={overlaySource}
      trackingState={3}
      trackingBlockedReason={null}
      confidence={0.9}
      boundW={0.2}
      boundH={0.3}
      targetX={0.0}
      targetY={0.0}
      ageS={0.1}
      freshThresholdS={freshThresholdS}
      confidenceHistory={[0.9]}
      ageHistory={ageHistory}
    />,
  );
}

// Parse the polyline points string into [x, y] pairs of numbers. Far less
// fragile than substring matching: changes in coord precision or ordering
// won't silently make assertions pass. Throws if the polyline is missing.
function ageSparklineYs(html: string): number[] {
  const svgRe = new RegExp('aria-label="Age history sparkline"[^>]*>([\\s\\S]*?)</svg>');
  const svgMatch = svgRe.exec(html);
  if (!svgMatch) throw new Error("Age history sparkline svg not found in render");
  const lineRe = /class="sparkline__line"[^>]*points="([^"]+)"/;
  const lineMatch = lineRe.exec(svgMatch[1]);
  if (!lineMatch) throw new Error("Age history sparkline polyline not found");
  return lineMatch[1]
    .trim()
    .split(/\s+/)
    .map((pair) => {
      const [x, y] = pair.split(",").map(Number);
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        throw new Error(`bad point pair: ${pair}`);
      }
      return y;
    });
}

describe("TrackingSummary age sparkline scaling", () => {
  it("uses ageThreshold * 4 as the y-axis ceiling for the age history", () => {
    // freshThresholdS = 0.25 -> ceiling = 1.0. height = 52.
    // y for value=0:    52 * (1 - 0/1) = 52
    // y for value=0.25: 52 * (1 - 0.25/1) = 39
    // y for value=1.0:  52 * (1 - 1/1) = 0
    const ys = ageSparklineYs(
      render({ ageHistory: [0, 0.25, 1.0], freshThresholdS: 0.25 }),
    );
    expect(ys).toEqual([52, 39, 0]);
  });

  it("scales by index — doubling freshThresholdS halves the y for fixed value", () => {
    const narrowYs = ageSparklineYs(
      render({ ageHistory: [0.5], freshThresholdS: 0.25 }),
    );
    const wideYs = ageSparklineYs(
      render({ ageHistory: [0.5], freshThresholdS: 0.5 }),
    );
    // narrow: ceiling = 1.0 -> 0.5 -> y = 52 - 26 = 26
    // wide:   ceiling = 2.0 -> 0.5 -> y = 52 - 13 = 39
    expect(narrowYs).toEqual([26]);
    expect(wideYs).toEqual([39]);
  });

  it("substitutes the per-overlay VIS default when freshThresholdS=0", () => {
    // TrackingSummary substitutes 0.25 default for VIS — ceiling = 1.0.
    const ys = ageSparklineYs(
      render({ ageHistory: [1.0], freshThresholdS: 0, overlaySource: "VIS" }),
    );
    expect(ys).toEqual([0]);
  });

  it("substitutes the per-overlay TEL default (0.5) when freshThresholdS<=0", () => {
    // TEL default = 0.5 -> ceiling = 2.0. value=1.0 -> y = 52 - 26 = 26.
    const ys = ageSparklineYs(
      render({ ageHistory: [1.0], freshThresholdS: 0, overlaySource: "TEL" }),
    );
    expect(ys).toEqual([26]);
  });

  it("handles negative freshThresholdS by falling back to per-overlay default", () => {
    // Same code path as 0; pin the contract.
    const ys = ageSparklineYs(
      render({ ageHistory: [1.0], freshThresholdS: -0.5, overlaySource: "VIS" }),
    );
    expect(ys).toEqual([0]);
  });
});
