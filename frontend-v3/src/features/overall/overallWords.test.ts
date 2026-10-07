import assert from "node:assert/strict";
import { test } from "node:test";

import {
  candidateLabel,
  dateProblem,
  historyStart,
  inStageSinceWords,
  isPastDay,
  noRequirementsWords,
  noSnapshotsWords,
  podLaterThanProject,
  releaseName,
  teamDatesLine,
  timelineTitle,
} from "./overallWords.ts";

test("a release is named once, whether or not its name says release", () => {
  assert.equal(releaseName("Checkout 1.0"), "Release Checkout 1.0");
  assert.equal(releaseName("Release 1.1"), "Release 1.1");
  assert.equal(releaseName("release-2026.4"), "release-2026.4");
});

test("the timeline title reads 'Last day' for one snapshot, never 'Last 1 days'", () => {
  assert.equal(timelineTitle(1), "Last day");
  assert.equal(timelineTitle(30), "Last 30 days");
  assert.equal(timelineTitle(0), "Daily snapshots");
});

test("a delivery date needs a day, and not one more than a year back", () => {
  assert.equal(dateProblem("", "2026-10-06"), "Pick a date.");
  assert.equal(dateProblem("2026-10-30", "2026-10-06"), null);
  assert.equal(dateProblem("2025-10-06", "2026-10-06"), null);
  assert.equal(
    dateProblem("2025-10-05", "2026-10-06"),
    "A delivery date more than a year in the past is not a plan.",
  );
});

test("a pod planning after the project is flagged, a missing date is not", () => {
  assert.equal(podLaterThanProject("2026-11-03", "2026-10-30"), true);
  assert.equal(podLaterThanProject("2026-10-16", "2026-10-30"), false);
  assert.equal(podLaterThanProject("2026-11-03", null), false);
});

test("a release candidate says how many issues carry it and Jira's date", () => {
  const day = (iso: string) => `day ${iso}`;
  assert.equal(
    candidateLabel(
      { kind: "fix_version", value: "Checkout 1.0", issues: 4, release_date: "2026-10-30" },
      day,
    ),
    "Checkout 1.0 · 4 issues · Jira releases day 2026-10-30",
  );
  assert.equal(
    candidateLabel({ kind: "label", value: "payments", issues: 1, release_date: null }, day),
    "payments · 1 issue",
  );
});

test("a date that has gone by is told so, one that has not is left alone", () => {
  assert.equal(isPastDay("2026-10-05", "2026-10-07"), true);
  assert.equal(isPastDay("2026-10-07", "2026-10-07"), false);
  assert.equal(isPastDay("2026-10-09", "2026-10-07"), false);
  assert.equal(isPastDay(null, "2026-10-07"), false);
  assert.equal(isPastDay("2026-10-05", null), false);
  // A timestamp is judged by its day.
  assert.equal(isPastDay("2026-10-05T23:30:00Z", "2026-10-06"), true);
});

test("the team's dates line agrees with its count and flags a past date", () => {
  assert.equal(
    teamDatesLine({ dated: 1, undated: 3, latest_key: "INS-4" }, true),
    "latest of 1 dated item (INS-4), past its date · 3 without a date",
  );
  assert.equal(
    teamDatesLine({ dated: 4, undated: 0, latest_key: null }, false),
    "latest of 4 dated items",
  );
});

test("an empty requirements panel blames set-up only today, and says history on a past day", () => {
  assert.match(noRequirementsWords(null), /Admin → Delivery stages/);
  const past = noRequirementsWords("Mon 5 Oct");
  assert.match(past, /on Mon 5 Oct/);
  assert.doesNotMatch(past, /Admin/);
  assert.match(noSnapshotsWords(null), /No daily snapshots yet/);
  assert.match(noSnapshotsWords("Mon 5 Oct"), /goes back to Mon 5 Oct/);
});

test("a requirement that has not moved since the history began is not given a false 'since'", () => {
  const day = (iso: string) => `day ${iso}`;
  const short = [{ day: "2026-10-06" }, { day: "2026-10-07" }];
  const start = historyStart(short, 30);
  assert.equal(start, "2026-10-06");
  assert.equal(inStageSinceWords("2026-10-06", start, day), "day 2026-10-06 or earlier");
  assert.equal(inStageSinceWords("2026-10-07", start, day), "day 2026-10-07");
  assert.equal(inStageSinceWords(null, start, day), "—");
  // A full window may have cut older days off, so no day is flagged.
  const full = Array.from({ length: 30 }, (_, i) => ({
    day: `2026-09-${String(i + 1).padStart(2, "0")}`,
  }));
  assert.equal(historyStart(full, 30), null);
  assert.equal(inStageSinceWords("2026-09-01", null, day), "day 2026-09-01");
  assert.equal(historyStart([], 30), null);
});
