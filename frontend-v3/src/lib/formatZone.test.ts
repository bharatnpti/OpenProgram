import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDate, formatDay, formatTime } from "./format.ts";
import { formatInZone } from "./zones.ts";

// Its own file, because `node --test` runs each file in its own process: the
// zone set here cannot leak into another test.
process.env.TZ = "Asia/Kolkata";

test("a timestamp just after midnight in India reads as that day, beside its time", () => {
  const sent = "2026-10-06T18:37:00Z"; // 00:07 on Wed 7 Oct in India
  assert.equal(formatDay(sent), "Wed 7 Oct");
  assert.equal(formatDate(sent), "7 Oct 2026");
  assert.equal(formatTime(sent), "00:07");
  assert.equal(formatDay("2026-10-06T18:37:00.123456+00:00"), "Wed 7 Oct");
});

test("a send in a Berlin schedule reads in Berlin for a viewer in India, with the zone named", () => {
  const sent = "2026-10-05T19:00:00Z"; // 00:30 on Tue 6 Oct in India
  assert.equal(formatTime(sent), "00:30");
  assert.equal(formatInZone(sent, "Europe/Berlin"), "Mon 5 Oct 21:00 Europe/Berlin");
});

test("a calendar day is the same day in every zone", () => {
  assert.equal(formatDay("2026-10-06"), "Tue 6 Oct");
  assert.equal(formatDate("2026-10-06"), "6 Oct 2026");
});

test("a timestamp the browser cannot read falls back to its own date", () => {
  assert.equal(formatDay("2026-10-06Tlater"), "Tue 6 Oct");
});
