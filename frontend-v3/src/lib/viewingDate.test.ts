import assert from "node:assert/strict";
import { test } from "node:test";

import {
  choseToday,
  dayWords,
  formatDayLabel,
  guessToday,
  markTodayChosen,
  parseViewingDate,
  readOnlyReason,
  resolveViewingDate,
  showsCurrentState,
  todayIso,
  viewingDateNote,
  withViewingDateParam,
} from "./viewingDate.ts";

const TODAY = "2026-10-06";

test("a year typed digit by digit never opens year 202 on the way", () => {
  assert.equal(parseViewingDate("0202-10-06", TODAY), null);
  assert.equal(parseViewingDate("1999-12-31", TODAY), null);
  assert.equal(parseViewingDate("2000-01-01", TODAY), "2000-01-01");
});

test("until the server says, today is the later of the UTC and the browser's day", () => {
  // Whatever zone the tests run in, the guess is never earlier than either day.
  const now = new Date("2026-10-06T20:30:00Z");
  const guess = guessToday(now);
  assert.ok(guess >= todayIso(now));
  const pad = (n: number) => String(n).padStart(2, "0");
  const local = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  assert.ok(guess >= local);
  assert.ok(guess === local || guess === todayIso(now));
});

test("only a real day before today is a past day", () => {
  assert.equal(parseViewingDate("2026-09-28", TODAY), "2026-09-28");
  assert.equal(parseViewingDate(null, TODAY), null);
  assert.equal(parseViewingDate("", TODAY), null);
  assert.equal(parseViewingDate("2026-9-28", TODAY), null);
  assert.equal(parseViewingDate("garbage", TODAY), null);
  assert.equal(parseViewingDate("2026-02-30", TODAY), null, "not on the calendar");
  assert.equal(parseViewingDate(TODAY, TODAY), null, "today is no param at all");
  assert.equal(parseViewingDate("2026-10-07", TODAY), null, "a future day");
});

test("today is the UTC calendar day, as frontend-v2 counts it", () => {
  assert.equal(todayIso(new Date("2026-10-06T23:59:00Z")), "2026-10-06");
  assert.equal(todayIso(new Date("2026-10-07T00:00:00Z")), "2026-10-07");
});

test("an in-app link carries the chosen day; back and forward restore the entry", () => {
  const base = { carried: "2026-09-28", today: TODAY, choseToday: false };
  assert.deepEqual(resolveViewingDate({ ...base, raw: null, navigation: "PUSH" }), {
    past: "2026-09-28",
    param: "2026-09-28",
  });
  assert.deepEqual(resolveViewingDate({ ...base, raw: null, navigation: "REPLACE" }), {
    past: "2026-09-28",
    param: "2026-09-28",
  });
  assert.deepEqual(resolveViewingDate({ ...base, raw: null, navigation: "POP" }), {
    past: null,
    param: null,
  });
  assert.deepEqual(
    resolveViewingDate({ ...base, raw: null, navigation: "PUSH", choseToday: true }),
    { past: null, param: null },
    "Back to today is not undone by the carried day",
  );
  assert.deepEqual(resolveViewingDate({ ...base, raw: "2026-09-20", navigation: "POP" }), {
    past: "2026-09-20",
    param: "2026-09-20",
  });
  assert.deepEqual(
    resolveViewingDate({ ...base, raw: "2027-01-01", navigation: "PUSH" }),
    { past: null, param: null },
    "a future day in the URL is taken out of it",
  );
});

test("the param is set and cleared without touching the rest of the query", () => {
  assert.equal(withViewingDateParam("?view=risks", "2026-09-28"), "?view=risks&asOf=2026-09-28");
  assert.equal(withViewingDateParam("?view=risks&asOf=2026-09-28", null), "?view=risks");
  assert.equal(withViewingDateParam("?asOf=2026-09-28", null), "");
  assert.equal(withViewingDateParam("", "2026-09-28"), "?asOf=2026-09-28");
});

test("Back to today is marked in the router state, keeping what was there", () => {
  assert.equal(choseToday(markTodayChosen({ from: "palette" })), true);
  assert.deepEqual(markTodayChosen({ from: "palette" }), {
    from: "palette",
    viewingDateToday: true,
  });
  assert.equal(choseToday(null), false);
  assert.equal(choseToday({}), false);
});

test("a day reads the way every screen writes one, with the year only when it differs", () => {
  assert.equal(formatDayLabel("2026-10-05", TODAY), "Mon 5 Oct");
  assert.match(formatDayLabel("2025-12-31", TODAY), /^Wed 31 Dec 2025$/);
  assert.equal(dayWords(null), "today");
  assert.equal(dayWords("Mon 5 Oct"), "on Mon 5 Oct");
  assert.equal(
    readOnlyReason("Mon 5 Oct"),
    "You're viewing Mon 5 Oct. Go back to today to make changes.",
  );
});

test("Admin shows current configuration; the banner says so, and what else stays current", () => {
  assert.equal(showsCurrentState("/admin"), true);
  assert.equal(showsCurrentState("/admin/anything"), true);
  assert.equal(showsCurrentState("/administration"), false);
  assert.equal(showsCurrentState("/today"), false);
  assert.match(viewingDateNote("/admin"), /current configuration/);
  assert.match(viewingDateNote("/reports/project-checkout/daily"), /today's/);
  assert.match(viewingDateNote("/coordination"), /as they are now/);
  assert.match(viewingDateNote("/chat"), /as it is now/);
  assert.match(viewingDateNote("/delivery/pod/pod-payments"), /^Read-only/);
  assert.match(viewingDateNote("/reports/project-checkout/overall"), /^Read-only/);
});
