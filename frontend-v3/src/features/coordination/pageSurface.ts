// The page surface the Requests and Pod pages draw on. No runtime imports.

/**
 * The report pages' dark hook (`.op-viz`, set by `data-theme="dark"`), and, for
 * the shared parts these pages reuse (Panel, ChipPicker, Pill), their white card
 * and their ink button read as the dark surface and its text inside it. In the
 * light theme `--op-viz-surface` is white, so nothing changes there.
 */
export const PAGE_SURFACE =
  "op-viz [&_.bg-white]:bg-(--op-viz-surface) [&_.bg-ink.text-white]:text-(--op-viz-surface)";
