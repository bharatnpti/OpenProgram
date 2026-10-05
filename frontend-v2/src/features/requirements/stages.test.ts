import assert from "node:assert/strict";
import { test } from "node:test";

import { changeLabel, countTicks, percentLabel, stackColumns } from "./stages.ts";

test("change labels sign the difference and say nothing for none", () => {
  assert.equal(changeLabel(2), "+2");
  assert.equal(changeLabel(-3), "−3");
  assert.equal(changeLabel(0), "");
  assert.equal(changeLabel(null), "");
});

test("columns stack production at the base and share one scale", () => {
  const { columns, max } = stackColumns(
    [
      { day: "2026-10-04", counts: { production: 2, in_testing: 2 } },
      { day: "2026-10-05", counts: { production: 4, in_testing: 2, raised: 2 } },
    ],
    { width: 200, height: 100, gap: 2 },
  );

  assert.equal(max, 8);
  const today = columns[1];
  assert.deepEqual(
    today.segments.map((segment) => segment.stage),
    ["production", "in_testing", "raised"],
  );
  // Production fills its full share; the next segments give up the 2px gap.
  assert.equal(today.segments[0].height, 50);
  assert.equal(today.segments[0].y, 50);
  assert.equal(today.segments[1].height, 23);
  assert.equal(columns[0].total, 4);
  // Bars never fill their slot: at most 24px, or 70% of a narrow slot.
  assert.equal(today.width, 24);
});

test("a tiny count keeps a visible sliver after its gap", () => {
  const { columns } = stackColumns([{ day: "d", counts: { production: 99, raised: 1 } }], {
    width: 50,
    height: 100,
  });

  assert.equal(columns[0].segments[1].height, 1);
});

test("count ticks are round and reach the maximum", () => {
  assert.deepEqual(countTicks(7), [0, 2, 4, 6, 8]);
  assert.deepEqual(countTicks(40), [0, 10, 20, 30, 40]);
  assert.deepEqual(countTicks(0), [0]);
});

test("percent labels round and admit when nothing is counted", () => {
  assert.equal(percentLabel(33.4), "33%");
  assert.equal(percentLabel(null), "—");
});
