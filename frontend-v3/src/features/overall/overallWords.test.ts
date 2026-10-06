import assert from "node:assert/strict";
import { test } from "node:test";

import {
  candidateLabel,
  dateProblem,
  podLaterThanProject,
  releaseName,
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
