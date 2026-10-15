import assert from "node:assert/strict";
import { test } from "node:test";

import { momentum, momentumNote } from "./momentum.ts";

const point = (rag: "red" | "amber" | "green" | "unknown") => ({
  as_of: "2026-10-01",
  rag,
  source: "confirmed" as const,
  score: { unknown: 0, red: 1, amber: 2, green: 3 }[rag],
});

test("momentum is read over reported days only, and says where it stands now", () => {
  assert.equal(momentum([point("red"), point("red"), point("red")]).label, "steady, now red");
  assert.equal(
    momentum([point("unknown"), point("red"), point("amber"), point("green")]).label,
    "improving, now green",
  );
  assert.equal(momentum([point("green"), point("amber")]).label, "sliding, now amber");
  // Opening unknown then going red must not draw as a climb.
  assert.equal(
    momentum([point("unknown"), point("unknown"), point("red")]).label,
    "not enough reported days",
  );
  assert.equal(momentum(undefined).label, "not enough reported days");
  // An unknown day is a gap in the line, never a value at the bottom of it.
  assert.deepEqual(momentum([point("unknown"), point("red"), point("green")]).values, [null, 0, 1]);
});

test("the note says how many of the days the direction rests on", () => {
  const days = [
    ...Array.from({ length: 25 }, () => point("unknown")),
    point("amber"),
    point("amber"),
    point("amber"),
    point("amber"),
    point("amber"),
  ];
  assert.equal(momentumNote(momentum(days)), "30 days · 5 days reported · steady, now amber");
  const full = Array.from({ length: 30 }, () => point("green"));
  assert.equal(momentumNote(momentum(full)), "30 days · steady, now green");
  assert.equal(momentumNote(momentum(undefined)), "30 days · not enough reported days");
  // The history keeps working days only, so it can hold fewer entries than the window has days.
  assert.equal(
    momentumNote(momentum([point("unknown"), point("red")]), 14),
    "14 days · 1 day reported · not enough reported days",
  );
});
