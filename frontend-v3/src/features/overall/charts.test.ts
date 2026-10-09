import assert from "node:assert/strict";
import { test } from "node:test";

import { countTicks, niceCeiling, thinAxisLabels } from "./charts.ts";

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

test("axis ceilings halve cleanly so the middle tick is a whole number", () => {
  assert.equal(niceCeiling(1), 2);
  assert.equal(niceCeiling(17), 20);
  assert.equal(niceCeiling(24), 40);
  assert.equal(niceCeiling(170), 200);
  assert.equal(niceCeiling(55), 60);
});

test("count ticks are whole numbers: quarters when they divide, else halves", () => {
  assert.deepEqual(countTicks(20), [0, 5, 10, 15, 20]);
  assert.deepEqual(countTicks(10), [0, 5, 10]);
  assert.deepEqual(countTicks(6), [0, 3, 6]);
  assert.deepEqual(countTicks(2), [0, 1, 2]);
});
