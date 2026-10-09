// Axis helpers the Overall charts share (O1's slip chart, O3's cumulative flow).
// Pure, no imports, so `node --test` runs it directly. Components draw what the
// geometry modules return; no scale lives in JSX.

type Anchor = "start" | "middle" | "end";

export type AxisLabel = { x: number; label: string; anchor: Anchor };

// A date label at 11px is about this wide per character, and two labels keep at
// least this much air between them.
const LABEL_CHAR_WIDTH = 6.2;
const LABEL_GAP = 8;

/**
 * The axis labels that fit. `candidates` come most important first and each
 * names the x of its day; one is kept when it stays inside the drawing and
 * clears every label already kept, else it is dropped. Two days a few pixels
 * apart ("6 Oct" and "7 Oct" under an axis that runs weeks on) print as "76Oct"
 * otherwise. A label near an edge turns to read inward instead of off the page.
 * Returned left to right.
 */
export function thinAxisLabels(
  candidates: { x: number; label: string; anchor: Anchor }[],
  width: number,
): AxisLabel[] {
  const kept: { label: AxisLabel; from: number; to: number }[] = [];
  for (const candidate of candidates) {
    const span = candidate.label.length * LABEL_CHAR_WIDTH;
    let anchor = candidate.anchor;
    const reach = (a: Anchor) =>
      a === "start"
        ? { from: candidate.x, to: candidate.x + span }
        : a === "end"
          ? { from: candidate.x - span, to: candidate.x }
          : { from: candidate.x - span / 2, to: candidate.x + span / 2 };
    if (reach(anchor).from < 0) anchor = "start";
    else if (reach(anchor).to > width) anchor = "end";
    const { from, to } = reach(anchor);
    const clear = kept.every(
      (other) => to + LABEL_GAP <= other.from || from >= other.to + LABEL_GAP,
    );
    if (clear) kept.push({ label: { ...candidate, anchor }, from, to });
  }
  return kept.sort((a, b) => a.label.x - b.label.x).map((entry) => entry.label);
}

/** Round up to a value that halves cleanly: 1, 2, 4, 6, 10, 20, 40, 60, 100… */
export function niceCeiling(value: number): number {
  if (value <= 2) return 2;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 4, 6, 10]) {
    if (step * magnitude >= value) return step * magnitude;
  }
  return 10 * magnitude;
}

/** Whole-number ticks from 0 to `max`: quarters when they are whole, else halves. */
export function countTicks(max: number): number[] {
  const step = max % 4 === 0 ? max / 4 : max / 2;
  const ticks: number[] = [];
  for (let value = 0; value <= max + 1e-9; value += step) ticks.push(value);
  return ticks;
}
