import assert from "node:assert/strict";
import { test } from "node:test";

import { clockTime, daysBetween, progressWidth, signedChange, weekdaysLabel } from "./format.ts";

test("a run of weekdays reads as a range, a broken run as a list", () => {
  assert.equal(weekdaysLabel([0, 1, 2, 3, 4]), "Mon–Fri");
  assert.equal(weekdaysLabel([4, 0, 2]), "Mon, Wed, Fri");
  assert.equal(weekdaysLabel([0, 1]), "Mon, Tue");
  assert.equal(weekdaysLabel([0, 1, 2, 3, 4, 5, 6]), "every day");
  assert.equal(weekdaysLabel([]), "no days");
});

test("a change is signed, and nothing to compare against says nothing", () => {
  assert.equal(signedChange(2), "+2");
  assert.equal(signedChange(-1), "−1");
  assert.equal(signedChange(0), "±0");
  assert.equal(signedChange(null), "");
});

test("days between two ISO days ignores the time of day", () => {
  assert.equal(daysBetween("2026-10-06", "2026-10-30"), 24);
  assert.equal(daysBetween("2026-10-06T23:59:00", "2026-10-07"), 1);
  assert.equal(daysBetween("2026-10-06", "2026-10-01"), -5);
});

test("progress never draws outside the bar", () => {
  assert.equal(progressWidth(58.3), 58.3);
  assert.equal(progressWidth(140), 100);
  assert.equal(progressWidth(-3), 0);
  assert.equal(progressWidth(null), 0);
});

test("a typed time is a time on the 24-hour clock, or it is not one", () => {
  assert.equal(clockTime("18:00"), "18:00");
  assert.equal(clockTime("9:00"), "09:00");
  assert.equal(clockTime("900"), "09:00");
  assert.equal(clockTime("0930"), "09:30");
  assert.equal(clockTime(" 17:30 "), "17:30");
  assert.equal(clockTime("9"), "09:00");
  assert.equal(clockTime("00:00"), "00:00");
  assert.equal(clockTime("23:59"), "23:59");
  // Not times: the field says so instead of saving them.
  assert.equal(clockTime("24:00"), null);
  assert.equal(clockTime("18:75"), null);
  assert.equal(clockTime("6 PM"), null);
  assert.equal(clockTime("06:00 PM"), null);
  assert.equal(clockTime(""), null);
});
