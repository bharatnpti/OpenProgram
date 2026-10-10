import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDay } from "../../lib/format.ts";
import {
  changeKeys,
  dateTicks,
  rangeWords,
  slipDrawable,
  slipFinding,
  slipGeometry,
  slipRecord,
} from "./slip.ts";

const change = (on: string, target: string | null, note = "", who = "Mina Patel") => ({
  target_date: target,
  changed_at: `${on}T12:00:00Z`,
  changed_by_name: who,
  note,
});

const set = change("2026-09-14", "2026-12-01", "First plan from the kickoff.");
const moved = change("2026-10-07", "2026-12-15", "Agreed with the business.");

// en-GB writes September "Sep" or "Sept" by ICU version: September days are spelt by formatDay.
const sep = (day: number) => formatDay(`2026-09-${String(day).padStart(2, "0")}`);

/** A forecast that narrows from 12–30 Dec on 24 Sep to 2–19 Dec today (9 Oct). */
function forecastDays() {
  const days: { day: string; p50: string | null; p85: string | null }[] = [];
  for (let d = 10; d <= 39; d++) {
    const day = new Date(Date.UTC(2026, 8, d)).toISOString().slice(0, 10);
    const t = (d - 24) / (39 - 24);
    const shift = (base: number, end: number) =>
      new Date(Date.UTC(2026, 11, Math.round(base + (end - base) * t))).toISOString().slice(0, 10);
    days.push(
      d < 24 ? { day, p50: null, p85: null } : { day, p50: shift(12, 2), p85: shift(30, 19) },
    );
  }
  return days;
}

test("each change reads as a key: set, moved by how much, cleared, set again", () => {
  const keys = changeKeys([
    set,
    moved,
    change("2026-10-08", null, "Scope under review."),
    change("2026-10-09", "2026-12-18"),
  ]);
  assert.deepEqual(keys[0], [
    "",
    "Tue 1 Dec",
    `, set by Mina Patel on ${sep(14)}: “First plan from the kickoff.”`,
  ]);
  assert.deepEqual(keys[1], [
    "Moved to ",
    "Tue 15 Dec",
    " by Mina Patel on Wed 7 Oct, 14 days later: “Agreed with the business.”",
  ]);
  assert.deepEqual(keys[2], ["", "Cleared", " by Mina Patel on Thu 8 Oct: “Scope under review.”"]);
  assert.deepEqual(keys[3], ["Set again to ", "Fri 18 Dec", " by Mina Patel on Fri 9 Oct."]);
});

test("the chart waits for a move or a forecast; one date and no history is a line", () => {
  assert.equal(slipDrawable([moved], []), false);
  assert.equal(slipDrawable([moved], [{ day: "2026-10-09", p50: null, p85: null }]), false);
  assert.equal(slipDrawable([set, moved], []), true);
  // One day of forecast is today's, which the strip shows; two are a history.
  const today = { day: "2026-10-09", p50: "2026-12-02", p85: "2026-12-21" };
  assert.equal(slipDrawable([moved], [today]), false);
  assert.equal(slipDrawable([moved], [{ ...today, day: "2026-10-08" }, today]), true);
  // The same date saved again with a new note is no step.
  assert.equal(slipDrawable([moved, { ...moved, note: "again" }], []), false);
});

test("the one line says the date and why, or that none is committed, and when the chart draws", () => {
  const dated = slipRecord([moved], null, 3, 10);
  assert.deepEqual(dated.line, [
    "",
    "Tue 15 Dec",
    ", set by Mina Patel on Wed 7 Oct: “Agreed with the business.”",
  ]);
  assert.equal(
    dated.note,
    "The chart draws once the date moves or the forecast has 10 working days of history. It has 3.",
  );
  assert.deepEqual(slipRecord([], "2026-10-30", 0, 10).line, [
    "No date is committed; the Jira release date ",
    "Fri 30 Oct",
    " is used.",
  ]);
  const none = slipRecord([], null, 0, 10);
  assert.deepEqual(none.line, ["", "No delivery date is committed yet.", ""]);
  assert.match(none.note, /^The chart draws once a date is committed and moves/);
});

test("the one line names the working days the tenant's forecast needs", () => {
  assert.equal(
    slipRecord([moved], null, 3, 5).note,
    "The chart draws once the date moves or the forecast has 5 working days of history. It has 3.",
  );
  assert.equal(
    slipRecord([], null, 3, 20).note,
    "The chart draws once a date is committed and moves, or the forecast has 20 working days of history. It has 3.",
  );
});

test("the finding says when the date moved and where it sits in the forecast", () => {
  assert.equal(
    slipFinding([set, moved], forecastDays(), "2026-09-10"),
    `How the date moved, ${sep(10)} to today. The committed date was set on ${sep(14)} to Tue 1 Dec, and moved on Wed 7 Oct to Tue 15 Dec. The forecast, drawn from ${sep(24)}, narrowed from 12 to 30 Dec down to 2 to 19 Dec; the committed date sits inside it.`,
  );
  assert.match(
    slipFinding([], [], "2026-10-01"),
    /No delivery date has been committed\. The forecast has too little history to draw yet\.$/,
  );
});

test("a range across a month names both months", () => {
  assert.equal(rangeWords("2026-11-28", "2026-12-21"), "28 Nov to 21 Dec");
  assert.equal(rangeWords("2026-12-02", "2026-12-21"), "2 to 21 Dec");
});

test("the chart starts at the first change or forecast day and hatches the days with no date", () => {
  const g = slipGeometry([set, moved], forecastDays(), "2026-10-09");
  assert.equal(g.start, "2026-09-10");
  assert.equal(g.todayX, g.size.plotRight);
  // Before the first commitment on 14 Sep there was no date: one hatched span from the left edge.
  assert.equal(g.noDate.length, 1);
  assert.equal(g.noDate[0].x, g.size.left);
  assert.ok(g.noDate[0].width > 0);
  // One unbroken step line, two numbered markers, one forecast band.
  assert.equal(g.committed.length, 1);
  assert.match(g.committed[0], /^M[\d.]+ [\d.]+ H[\d.]+ V[\d.]+ H[\d.]+$/);
  assert.deepEqual(
    g.marks.map((m) => m.n),
    [1, 2],
  );
  assert.equal(g.bands.length, 1);
  assert.equal(g.bandStart?.day, "2026-09-24");
  // The labels at today keep 14 px apart, p85 on top, p50 at the bottom.
  assert.deepEqual(
    g.right.map((r) => r.text),
    ["p85 · Sat 19 Dec", "Committed · Tue 15 Dec", "p50 · Wed 2 Dec"],
  );
  for (let i = 1; i < g.right.length; i++) assert.ok(g.right[i].y - g.right[i - 1].y >= 14);
});

test("a cleared date breaks the line and hatches until it is set again", () => {
  const g = slipGeometry(
    [set, change("2026-09-21", null), change("2026-09-28", "2026-12-15")],
    [],
    "2026-10-09",
  );
  assert.equal(g.committed.length, 2);
  assert.equal(g.noDate.length, 1);
  assert.equal(g.noDate[0].title, `Cleared on ${sep(21)}`);
});

test("date ticks fall on the 1st and the 15th inside the range", () => {
  assert.deepEqual(dateTicks("2026-11-20", "2027-01-05"), [
    "2026-12-01",
    "2026-12-15",
    "2027-01-01",
  ]);
});
