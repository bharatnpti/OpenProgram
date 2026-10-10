// The inline styles Overall's visuals share, in the theme's tokens: the bypassed
// hatch and the SVG text styles. Inline styles read the --op-* tokens, which the
// dark theme hook and the .op-viz remap in index.css both reach.
import type { CSSProperties } from "react";

/** The bypassed hatch, the one pattern a cell or a swatch is drawn with. */
export const HATCH: CSSProperties = {
  background:
    "repeating-linear-gradient(135deg, var(--op-red-bg) 0 6px, var(--op-viz-red-hatch) 6px 8px)",
};

/** SVG text styles, in the theme's tokens. */
export const SVG_TEXT = {
  muted: { fill: "var(--op-grey-secondary)", fontSize: 11.5 },
  ink: { fill: "var(--op-black)", fontSize: 12.5, fontWeight: 700 },
  red: { fill: "var(--op-red)", fontSize: 12, fontWeight: 700 },
  amber: { fill: "var(--op-amber)", fontSize: 12, fontWeight: 700 },
} satisfies Record<string, CSSProperties>;
