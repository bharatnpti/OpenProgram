import assert from "node:assert/strict";
import { test } from "node:test";

import { burndownByCount, burndownGeometry, niceCeiling, stageStack } from "./charts.ts";

const timeline = [
  {
    day: "2026-10-01",
    counts: {
      raised: 4,
      groomed: 3,
      in_development: 5,
      in_testing: 2,
      business_testing: 1,
      production: 1,
    },
  },
  {
    day: "2026-10-06",
    counts: {
      raised: 2,
      groomed: 4,
      in_development: 6,
      in_testing: 3,
      business_testing: 2,
      production: 7,
    },
  },
];

test("the burn-down by count is every requirement not yet in production", () => {
  assert.deepEqual(burndownByCount(timeline), [
    { day: "2026-10-01", remaining: 15 },
    { day: "2026-10-06", remaining: 17 },
  ]);
});

test("a committed date after today stretches the axis so its marker stays on the chart", () => {
  const points = burndownByCount(timeline);
  const geometry = burndownGeometry(
    points,
    [{ key: "committed", day: "2026-10-30", label: "Committed" }],
    (d) => d,
  );
  const marker = geometry.markers[0];
  assert.ok(geometry.last);
  assert.ok(marker.x > geometry.last.x, "committed date is to the right of today");
  assert.ok(marker.x <= geometry.width, "and still inside the drawing");
  assert.equal(geometry.xLabels.at(-1)?.label, "2026-10-30");
});

test("no history draws nothing rather than a flat green line", () => {
  const geometry = burndownGeometry([], [], (d) => d);
  assert.equal(geometry.path, "");
  assert.equal(geometry.last, null);
});

test("axis ceilings halve cleanly so the middle tick is a whole number", () => {
  assert.equal(niceCeiling(1), 2);
  assert.equal(niceCeiling(17), 20);
  assert.equal(niceCeiling(24), 40);
  assert.equal(niceCeiling(170), 200);
  assert.equal(niceCeiling(55), 60);
});

test("a stacked day puts production on top and skips empty stages", () => {
  const order = [
    "raised",
    "groomed",
    "in_development",
    "in_testing",
    "business_testing",
    "production",
  ] as const;
  const { bars, yMax } = stageStack(
    [{ day: "2026-10-06", counts: { raised: 0, production: 3, in_testing: 1 } }],
    [...order],
  );
  assert.equal(yMax, 4);
  assert.deepEqual(
    bars[0].segments.map((s) => s.stage),
    ["in_testing", "production"],
  );
  const [testing, production] = bars[0].segments;
  assert.ok(production.y < testing.y, "production sits above in testing");
});
