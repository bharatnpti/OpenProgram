import assert from "node:assert/strict";
import { test } from "node:test";

import {
  burndownByCount,
  burndownByPoints,
  burndownGeometry,
  burndownSeries,
  niceCeiling,
  stageStack,
  thinAxisLabels,
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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const shortDay = (iso: string) =>
  `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;

test("two days a few pixels apart under a long axis print one label, not '76Oct'", () => {
  // Two snapshots, a day apart, and a committed date and a forecast weeks on: the
  // axis is 70 days wide, so 6 Oct and 7 Oct sit about seven pixels from each other.
  const geometry = burndownGeometry(
    [
      { day: "2026-10-06", remaining: 18 },
      { day: "2026-10-07", remaining: 18 },
    ],
    [
      { key: "committed", day: "2026-12-01", label: "Committed" },
      { key: "p85", day: "2026-12-15", label: "85% likely" },
    ],
    shortDay,
  );
  // The first, the committed date and the last stay; today's label, which sat on the first, goes.
  assert.deepEqual(
    geometry.xLabels.map((label) => label.label),
    ["6 Oct", "1 Dec", "15 Dec"],
  );
  assert.deepEqual(
    geometry.xLabels.map((label) => label.anchor),
    ["start", "middle", "end"],
  );
});

test("a day far enough from the first keeps its label", () => {
  const geometry = burndownGeometry(
    [
      { day: "2026-10-06", remaining: 18 },
      { day: "2026-10-20", remaining: 12 },
    ],
    [{ key: "committed", day: "2026-10-30", label: "Committed" }],
    shortDay,
  );
  // 14 days of a 24-day axis: today is well clear of both ends.
  assert.deepEqual(
    geometry.xLabels.map((label) => label.label),
    ["6 Oct", "20 Oct", "30 Oct"],
  );
});

test("a label never overlaps another, and the most important one wins a clash", () => {
  const labels = thinAxisLabels(
    [
      { x: 36, label: "6 Oct", anchor: "start" },
      { x: 544, label: "15 Dec", anchor: "end" },
      { x: 520, label: "13 Dec", anchor: "middle" },
      { x: 43, label: "7 Oct", anchor: "middle" },
      { x: 300, label: "20 Nov", anchor: "middle" },
    ],
    560,
  );
  assert.deepEqual(
    labels.map((label) => label.label),
    ["6 Oct", "20 Nov", "15 Dec"],
  );
  // Left to right, so the figure reads in order.
  assert.deepEqual(
    labels.map((label) => label.x),
    [36, 300, 544],
  );
});

test("a label at an edge reads inward instead of running off the drawing", () => {
  const [near] = thinAxisLabels([{ x: 556, label: "15 Dec", anchor: "middle" }], 560);
  assert.equal(near.anchor, "end");
  const [far] = thinAxisLabels([{ x: 4, label: "6 Oct", anchor: "middle" }], 560);
  assert.equal(far.anchor, "start");
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
