import assert from "node:assert/strict";
import { test } from "node:test";

import {
  burndownByCount,
  burndownByPoints,
  burndownGeometry,
  burndownSeries,
  niceCeiling,
  stageStack,
} from "./charts.ts";

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
    has_points: false,
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
    has_points: false,
  },
];

test("the burn-down is by story points only when every day kept them", () => {
  const pointed = timeline.map((point, i) => ({
    ...point,
    has_points: true,
    points: { raised: 10, in_development: 20.5 - i * 8, production: 30 + i * 8 },
  }));
  assert.deepEqual(burndownByPoints(pointed), [
    { day: "2026-10-01", remaining: 30.5 },
    { day: "2026-10-06", remaining: 22.5 },
  ]);
  assert.equal(burndownSeries(pointed).unit, "story points");
  // One day without points (today counted live with an unpointed story) falls back to the count.
  const mixed = [pointed[0], { ...pointed[1], has_points: false }];
  assert.equal(burndownByPoints(mixed), null);
  assert.deepEqual(burndownSeries(mixed), {
    points: burndownByCount(mixed),
    unit: "requirements",
    caption: "Requirements not yet in production, by count: not every requirement has story points",
  });
  // With no snapshots there is nothing to say about points either way.
  assert.equal(burndownSeries([]).caption, "Requirements not yet in production, by count");
});

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
    [{ day: "2026-10-06", counts: { raised: 0, production: 3, in_testing: 1 }, has_points: false }],
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
