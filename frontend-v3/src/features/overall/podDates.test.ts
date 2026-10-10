import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDay } from "../../lib/format.ts";
import { datesLegend, datesLegendWords, podChangeKeys, podDates, podFact } from "./podDates.ts";

type Verdict = "on_track" | "at_risk" | "off_track" | "done" | "no_date" | "not_enough_data";

function pod(
  id: string,
  name: string,
  over: {
    target?: string | null;
    verdict: Verdict;
    latest?: string | null;
    key?: string | null;
    undated?: number;
    total?: number;
    changes?: {
      target_date: string | null;
      changed_at: string;
      changed_by_name: string;
      note: string;
    }[];
  },
) {
  return {
    scope_id: id,
    name,
    target: over.target ?? null,
    target_source: over.target ? ("committed" as const) : null,
    verdict: over.verdict,
    team: {
      latest: over.latest ?? null,
      latest_key: over.key ?? null,
      dated: 1,
      undated: over.undated ?? 0,
    },
    total: over.total ?? 5,
    open: 3,
    history: {
      p50: null,
      p85: null,
      remaining: 3,
      unit: "requirements",
      sample_days: 3,
      completed_in_sample: 0,
      reason: null,
      needed_days: 10,
    },
    commitment: {
      target_date: over.target ?? null,
      original_date: over.target ?? null,
      times_moved: 0,
      moved_days: null,
      changes: (over.changes ?? []).map((change) => ({ ...change, changed_by: "U1006" })),
    },
  };
}

const TODAY = "2026-10-09";
const payments = pod("pod-payments", "Payments Pod", {
  target: "2026-11-20",
  verdict: "at_risk",
  undated: 3,
  changes: [
    {
      target_date: "2026-11-20",
      changed_at: "2026-10-07T12:00:00Z",
      changed_by_name: "Ira Novak",
      note: "Buffer for payment provider cert.",
    },
  ],
});
const platform = pod("pod-platform", "Platform Pod", { verdict: "no_date", undated: 1 });
const storefront = pod("pod-storefront", "Storefront Pod", {
  verdict: "no_date",
  latest: "2026-10-07",
  key: "CHK-14",
});

test("each pod's date sits on one track from today to past the project's date", () => {
  const dates = podDates([payments, platform, storefront], "2026-12-15", TODAY);
  const [p, pl, s] = dates.rows;
  assert.ok(dates.project !== null && dates.project > 75 && dates.project < 100);
  assert.ok(p.at !== null && p.at < (dates.project ?? 0), "20 Nov is before 15 Dec");
  assert.equal(p.tone, "warning");
  assert.deepEqual(p.chip, { label: "At risk", tone: "warning" });
  assert.equal(pl.at, null);
  assert.deepEqual(pl.chip, { label: "No committed date", tone: "danger" });
  assert.equal(s.fact, "The team's latest date, Wed 7 Oct (CHK-14), is past");
  // Month starts along the way, none on top of Today or of the project's label.
  assert.deepEqual(
    dates.ticks.map((tick) => tick.label),
    ["1 Nov", "1 Dec"],
  );
  assert.equal(
    dates.spoken,
    "Pod dates against the project date, Tue 15 Dec: Payments Pod Fri 20 Nov, at risk; Platform Pod has no committed date; Storefront Pod has no committed date",
  );
});

test("a pod's fact names its date against the project's, or why it has none", () => {
  assert.equal(
    podFact(payments, "2026-12-15", TODAY),
    "Fri 20 Nov · 25 days before the project date",
  );
  assert.equal(
    podFact(pod("x", "X", { target: "2026-12-22", verdict: "off_track" }), "2026-12-15", TODAY),
    "Tue 22 Dec · 7 days after the project date",
  );
  assert.equal(podFact(payments, null, TODAY), "Fri 20 Nov");
  assert.equal(podFact(platform, "2026-12-15", TODAY), "1 open requirement has no ETA or due date");
  assert.equal(
    podFact(pod("y", "Y", { verdict: "done", total: 0 }), "2026-12-15", TODAY),
    "No requirements are counted for it yet.",
  );
});

test("a pod with nothing counted is grey, not red", () => {
  const [row] = podDates([pod("y", "Y", { verdict: "done", total: 0 })], null, TODAY).rows;
  assert.deepEqual(row.chip, { label: "Nothing in scope", tone: "neutral" });
  assert.equal(row.tone, "neutral");
});

test("the pods' changes read newest first, each led by its pod", () => {
  const moved = pod("pod-storefront", "Storefront Pod", {
    target: "2026-12-01",
    verdict: "on_track",
    changes: [
      {
        target_date: "2026-11-27",
        changed_at: "2026-09-30T12:00:00Z",
        changed_by_name: "Ben Sorensen",
        note: "",
      },
      {
        target_date: "2026-12-01",
        changed_at: "2026-10-08T12:00:00Z",
        changed_by_name: "Ben Sorensen",
        note: "Vendor delay.",
      },
    ],
  });
  assert.deepEqual(podChangeKeys([payments, moved]), [
    [
      "",
      "Storefront Pod",
      ", moved to Tue 1 Dec by Ben Sorensen on Thu 8 Oct, 4 days later: “Vendor delay.”",
    ],
    [
      "",
      "Payments Pod",
      ", set to Fri 20 Nov by Ira Novak on Wed 7 Oct: “Buffer for payment provider cert.”",
    ],
    ["", "Storefront Pod", `, set to Fri 27 Nov by Ben Sorensen on ${formatDay("2026-09-30")}.`],
  ]);
});

test("the legend names only the marks the tracks draw, in the colours they are drawn in", () => {
  // Pods with no date: only the dashed line, no project mark, and no swatch of any colour.
  const none = datesLegend([platform, storefront], [], null);
  assert.deepEqual(none, { project: false, whose: [], tones: [], noDate: true });
  assert.equal(datesLegendWords(none), null);

  // One dated pod, at risk: its swatch is amber because the pod is, and nothing is undated.
  const one = datesLegend([payments], [], "2026-12-15");
  assert.deepEqual(one, { project: true, whose: ["pod"], tones: ["warning"], noDate: false });
  assert.equal(datesLegendWords(one), "A pod's date, in its verdict's colour");

  // Mixed: the colours present, worst first, and the dashed line for the pod without a date.
  const onTrack = pod("pod-x", "X Pod", { target: "2026-11-27", verdict: "on_track" });
  const offTrack = pod("pod-y", "Y Pod", { target: "2026-12-30", verdict: "off_track" });
  const mixed = datesLegend([payments, onTrack, offTrack, platform], [], "2026-12-15");
  assert.deepEqual(mixed.tones, ["danger", "warning", "success"]);
  assert.equal(mixed.noDate, true);
});

test("a date drawn grey is said to be grey, and a release's date is named as one", () => {
  const empty = pod("z", "Z", { target: "2026-11-30", verdict: "done", total: 0 });
  const grey = datesLegend([empty], [], "2026-12-15");
  assert.deepEqual(grey.tones, ["neutral"]);
  assert.equal(datesLegendWords(grey), "A pod's date, grey, with no verdict to colour it");

  const release = pod("rel-1", "Release 1", { target: "2026-12-01", verdict: "on_track" });
  const both = datesLegend([empty], [release], "2026-12-15");
  assert.deepEqual(both.whose, ["pod", "release"]);
  assert.deepEqual(both.tones, ["success", "neutral"]);
  assert.equal(
    datesLegendWords(both),
    "A pod's or release's date, in its verdict's colour, grey with none",
  );
  // Only the release has a date: the legend does not speak of pods.
  const onlyRelease = datesLegend([platform], [release], null);
  assert.deepEqual(onlyRelease.whose, ["release"]);
  assert.equal(datesLegendWords(onlyRelease), "A release's date, in its verdict's colour");
});
