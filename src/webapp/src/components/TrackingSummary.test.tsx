import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TrackingSummary from "./TrackingSummary";

interface RenderArgs {
  ageHistory: number[];
  freshThresholdS: number;
}

function render({ ageHistory, freshThresholdS }: RenderArgs): string {
  return renderToString(
    <TrackingSummary
      overlaySource="VIS"
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

// Pull the polyline points attr for a given sparkline label out of the SSR
// output. Returns null if the polyline isn't present.
function pointsFor(html: string, ariaLabel: string): string | null {
  // Find the svg with this aria-label, then the inner polyline that's NOT the
  // baseline (which uses class sparkline__baseline).
  const svgRe = new RegExp(
    `aria-label="${ariaLabel}"[^>]*>([\\s\\S]*?)</svg>`,
  );
  const svgMatch = svgRe.exec(html);
  if (!svgMatch) return null;
  const inner = svgMatch[1];
  const lineRe = /class="sparkline__line"[^>]*points="([^"]+)"/;
  const lineMatch = lineRe.exec(inner);
  return lineMatch ? lineMatch[1] : null;
}

describe("TrackingSummary age sparkline scaling", () => {
  it("uses freshThresholdS * 4 as the y-axis ceiling for the age history", () => {
    // freshThresholdS = 0.25 → ceiling = 1.0. A value of 1.0 should sit at
    // y=0 (top of sparkline); a value of 0.25 should sit at y = height *
    // 0.75 (one-quarter up).
    const html = render({
      ageHistory: [0, 0.25, 1.0],
      freshThresholdS: 0.25,
    });
    const points = pointsFor(html, "Age history sparkline");
    expect(points).not.toBeNull();
    // Sparkline height = 52; ceiling = 1.0 (= 0.25 * 4).
    // y for value=0:    52 - 0/1 * 52 = 52
    // y for value=0.25: 52 - 0.25/1 * 52 = 39
    // y for value=1.0:  52 - 1.0/1 * 52 = 0
    expect(points).toContain("52.00");
    expect(points).toContain("39.00");
    expect(points).toContain("0.00");
  });

  it("scales differently when freshThresholdS doubles", () => {
    const htmlNarrow = render({
      ageHistory: [0.5],
      freshThresholdS: 0.25, // ceiling = 1.0 → 0.5 sits at y = 52 - 26 = 26.00
    });
    const htmlWide = render({
      ageHistory: [0.5],
      freshThresholdS: 0.5, // ceiling = 2.0 → 0.5 sits at y = 52 - 13 = 39.00
    });
    const narrow = pointsFor(htmlNarrow, "Age history sparkline");
    const wide = pointsFor(htmlWide, "Age history sparkline");
    expect(narrow).not.toBe(wide);
    expect(narrow).toContain("26.00");
    expect(wide).toContain("39.00");
  });

  it("substitutes the per-overlay default when freshThresholdS<=0", () => {
    // TrackingSummary substitutes a 0.25 default for VIS when
    // freshThresholdS<=0 — the sparkline ceiling becomes 0.25 * 4 = 1.0.
    const html = render({
      ageHistory: [1.0],
      freshThresholdS: 0,
    });
    const points = pointsFor(html, "Age history sparkline");
    // value=1.0 at ceiling=1.0 -> normalized=1.0 -> y=0.
    expect(points).toContain("0.00");
  });
});
