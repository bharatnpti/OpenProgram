// Which items of a long list are near the screen. Pure, so `node --test` runs it.

export type Range = {
  first: number;
  last: number;
  /** The height of the items above `first`, and below `last`, in pixels. */
  before: number;
  after: number;
};

/**
 * The items whose box meets the window, `overscan` pixels either side, from
 * each item's height and where the window is (`top`: pixels scrolled past the
 * list's own top; negative while the list starts below the window's top).
 */
export function visibleRange(
  sizes: readonly number[],
  top: number,
  height: number,
  overscan: number,
): Range {
  if (sizes.length === 0) return { first: 0, last: -1, before: 0, after: 0 };
  const from = top - overscan;
  const to = top + height + overscan;
  let offset = 0;
  let first = -1;
  let last = -1;
  let before = 0;
  for (let index = 0; index < sizes.length; index += 1) {
    const end = offset + sizes[index];
    if (first === -1 && end > from) {
      first = index;
      before = offset;
    }
    if (offset < to) last = index;
    offset = end;
  }
  if (first === -1) {
    // Scrolled past the end: keep the last item drawn.
    first = sizes.length - 1;
    before = offset - sizes[first];
  }
  last = Math.max(last, first);
  const drawn = sizes.slice(first, last + 1).reduce((sum, size) => sum + size, 0);
  return { first, last, before, after: Math.max(0, offset - before - drawn) };
}
