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
  reasonsAfterCause,
  releaseName,
  teamDatesLine,
  timelineTitle,
  undatedWords,
  verdictCause,
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

// A scope as the server answers it, with only what the verdict rule reads.
const scope = (over: {
  verdict: "on_track" | "at_risk" | "off_track" | "done" | "no_date" | "not_enough_data";
  target?: string | null;
  history?: Partial<{ p50: string | null; p85: string | null; reason: string | null }>;
  team?: Partial<{
    latest: string | null;
    latest_key: string | null;
    dated: number;
    undated: number;
  }>;
}) => ({
  verdict: over.verdict,
  target: over.target === undefined ? "2026-12-15" : over.target,
  history: {
    p50: null,
    p85: null,
    remaining: 6,
    unit: "requirements",
    sample_days: 1,
    completed_in_sample: 0,
    reason: "Only 1 working day of history; a forecast needs 10.",
    ...over.history,
  },
  team: { latest: "2026-10-09", latest_key: "CHK-4", dated: 7, undated: 6, ...over.team },
});

test("the undated count is said in the singular too", () => {
  assert.equal(undatedWords(6), "6 open requirements have no ETA or due date");
  assert.equal(undatedWords(1), "1 open requirement has no ETA or due date");
});

test("at risk on a short history says it is the requirements with no date", () => {
  const cause = verdictCause(scope({ verdict: "at_risk" }));
  assert.equal(cause?.because, "At risk because 6 open requirements have no ETA or due date.");
  assert.match(cause?.also ?? "", /could finish later/);
  assert.match(cause?.also ?? "", /not enough history to forecast/);
  assert.equal(
    verdictCause(scope({ verdict: "at_risk", team: { undated: 1 } }))?.because,
    "At risk because 1 open requirement has no ETA or due date.",
  );
});

test("at risk on a history that forecasts says where the two dates fall", () => {
  const cause = verdictCause(
    scope({
      verdict: "at_risk",
      target: "2026-11-20",
      history: { p50: "2026-11-13", p85: "2026-12-02", reason: null },
      team: { undated: 4 },
    }),
  );
  assert.match(
    cause?.because ?? "",
    /^At risk because history is 50% likely to finish by .*13 Nov/,
  );
  assert.match(cause?.because ?? "", /85% likely only by .*2 Dec, after the delivery date\.$/);
  // The history decides, so the undated count is no part of it.
  assert.doesNotMatch(cause?.because ?? "", /ETA or due date/);
});

test("off track names the forecast or the team's latest date, whichever decided it", () => {
  assert.match(
    verdictCause(
      scope({
        verdict: "off_track",
        history: { p50: "2026-12-30", p85: "2027-01-20", reason: null },
      }),
    )?.because ?? "",
    /^Off track because history puts the finish at .*30 Dec \(50% likely\), after the delivery date\.$/,
  );
  assert.match(
    verdictCause(
      scope({ verdict: "off_track", team: { latest: "2027-01-08", latest_key: "CHK-9" } }),
    )?.because ?? "",
    /^Off track because the team's latest date, .*8 Jan \(CHK-9\), is after the delivery date\.$/,
  );
});

test("not enough to forecast adds that nothing has a date to go by", () => {
  const none = { latest: null, latest_key: null, dated: 0 };
  assert.equal(
    verdictCause(scope({ verdict: "not_enough_data", team: { ...none, undated: 4 } }))?.because,
    "Not enough history to forecast, and none of the 4 open requirements has an ETA or due date to go by.",
  );
  assert.equal(
    verdictCause(scope({ verdict: "not_enough_data", team: { ...none, undated: 1 } }))?.because,
    "Not enough history to forecast, and the one open requirement has no ETA or due date to go by.",
  );
  assert.equal(
    verdictCause(scope({ verdict: "not_enough_data", team: { ...none, undated: 0 } })),
    null,
  );
});

test("a verdict that needs no cause, or has no date to be late against, has none", () => {
  assert.equal(verdictCause(scope({ verdict: "on_track" })), null);
  assert.equal(verdictCause(scope({ verdict: "done" })), null);
  assert.equal(verdictCause(scope({ verdict: "no_date", target: null })), null);
  // A server that calls it at risk for a reason this rule does not know: no guess.
  assert.equal(verdictCause(scope({ verdict: "at_risk", team: { undated: 0 } })), null);
});

test("the server's own undated line is not said twice beside the cause", () => {
  const reasons = [
    "Committed for Tue 15 Dec 2026 by Mina Patel.",
    "Only 1 working day of history; a forecast needs 10.",
    "Team dates: the latest open requirement is due Fri 9 Oct 2026 (CHK-4).",
    "6 open requirements have no ETA or due date.",
  ];
  const cause = verdictCause(scope({ verdict: "at_risk" }));
  assert.deepEqual(reasonsAfterCause(reasons, cause), reasons.slice(0, 3));
  // Without such a cause nothing is taken out.
  assert.deepEqual(reasonsAfterCause(reasons, null), reasons);
  assert.deepEqual(
    reasonsAfterCause(
      reasons,
      verdictCause(
        scope({ verdict: "off_track", history: { p50: "2026-12-30", p85: "2027-01-20" } }),
      ),
    ),
    reasons,
  );
});
