import assert from "node:assert/strict";
import { test } from "node:test";

import { visibleRange } from "./windowing.ts";

const sizes = Array.from({ length: 200 }, () => 100);

test("only the items near the window are drawn, the rest held as spacers", () => {
  const range = visibleRange(sizes, 5_000, 800, 200);
  assert.deepEqual([range.first, range.last], [48, 59]);
  assert.equal(range.before, 4_800);
  assert.equal(range.before + (range.last - range.first + 1) * 100 + range.after, 20_000);
});

test("above the list the first items are drawn, past its end the last", () => {
  const above = visibleRange(sizes, -300, 800, 100);
  assert.equal(above.first, 0);
  assert.equal(above.before, 0);
  const past = visibleRange(sizes, 50_000, 800, 100);
  assert.equal(past.first, 199);
  assert.equal(past.after, 0);
  assert.deepEqual(visibleRange([], 0, 800, 100), { first: 0, last: -1, before: 0, after: 0 });
});

test("measured heights move the window", () => {
  const mixed = [500, 50, 50, 50, 500];
  const range = visibleRange(mixed, 520, 100, 0);
  assert.deepEqual([range.first, range.last], [1, 3]);
  assert.equal(range.before, 500);
  assert.equal(range.after, 500);
});
