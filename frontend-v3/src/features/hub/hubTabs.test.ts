import assert from "node:assert/strict";
import { test } from "node:test";

import type { DayReportResponse, ScopeProjectDto } from "../../api/schema";
import {
  badgeScope,
  hubHref,
  opensPodLevel,
  podChoices,
  podVerdict,
  reportsBadge,
  scopeBadge,
  scopeBadgeAt,
  tabAt,
  verdictTone,
  verdictWords,
  warningsBadge,
} from "./hubTabs.ts";

const EVERY = {
  all: ["date", "warnings", "scope", "reports"],
  project: ["date", "warnings", "scope", "reports"],
  pod: ["date", "warnings", "scope", "reports"],
} as const;
const DEVELOPER = {
  all: ["reports"],
  project: ["warnings", "scope", "reports"],
  pod: ["date", "warnings"],
} as const;

test("a level keeps the tab when it has it, else opens its first", () => {
  assert.equal(tabAt(EVERY, "pod", "scope"), "scope");
  assert.equal(tabAt(DEVELOPER, "pod", "scope"), "date");
  assert.equal(tabAt(DEVELOPER, "project", "date"), "warnings");
  assert.equal(tabAt(DEVELOPER, "all", null), "reports");
  assert.equal(tabAt({ ...EVERY, pod: [] }, "pod", "date"), null);
});

test("picking a pod keeps the tab, picking a tab keeps the pod, the day and release go along", () => {
  const from = { place: { level: "project" as const, projectId: "p" }, tab: "warnings" as const };
  const search = "?asOf=2026-10-02&release=rel-1&row=pod:x";
  assert.equal(
    hubHref(EVERY, from, { level: "pod", projectId: "p", podId: "x" }, from.tab, search),
    "/projects/p/pods/x/warnings?asOf=2026-10-02&release=rel-1",
  );
  // A developer's pod has no scope tab: its first.
  assert.equal(
    hubHref(DEVELOPER, { ...from, tab: "scope" }, { level: "pod", projectId: "p", podId: "x" }),
    "/projects/p/pods/x/date",
  );
  assert.equal(
    hubHref(EVERY, from, { level: "all" }, "reports", search),
    "/portfolio/reports?asOf=2026-10-02",
  );
});

test("the badges say each tab's summary, and nothing for none", () => {
  assert.deepEqual(warningsBadge(8), { text: "8", label: "8 open warnings" });
  assert.deepEqual(warningsBadge(1), { text: "1", label: "1 open warning" });
  assert.equal(warningsBadge(0), null);
  assert.equal(warningsBadge(undefined), null);
  assert.deepEqual(scopeBadge(2, 3), {
    text: "+2 · 3",
    label: "2 added after planning, 3 open Jira hygiene findings",
  });
  assert.deepEqual(scopeBadge(0, 1), { text: "1", label: "1 open Jira hygiene finding" });
  assert.deepEqual(scopeBadge(4, 0), { text: "+4", label: "4 added after planning" });
  assert.equal(scopeBadge(0, 0), null);
  assert.equal(scopeBadge(null, undefined), null);
});

const report = (lastRun: Partial<NonNullable<DayReportResponse["last_run"]>> | null) =>
  ({
    report_id: "rep-1",
    last_run: lastRun
      ? {
          run_id: "run-1",
          report_id: "rep-1",
          report_date: "2026-10-15",
          trigger: "schedule",
          status: "sent",
          started_at: "2026-10-15T17:29:00Z",
          finished_at: "2026-10-15T17:30:00Z",
          title: "Day report",
          outcomes: [],
          actor: null,
          actor_name: null,
          ...lastRun,
        }
      : null,
  }) as unknown as DayReportResponse;
const hhmm = (iso: string) => iso.slice(11, 16);

test("Reports says when today's day report went out, or that none is set up", () => {
  assert.equal(reportsBadge(undefined, "2026-10-15", hhmm), null, "still reading");
  assert.deepEqual(reportsBadge([], "2026-10-15", hhmm), {
    text: "Not set up",
    label: "No day report is set up",
  });
  assert.deepEqual(reportsBadge([report({})], "2026-10-15", hhmm), {
    text: "Sent 17:30",
    label: "Day report sent at 17:30",
  });
  // The latest of several reports sent that day.
  assert.equal(
    reportsBadge(
      [report({}), report({ finished_at: "2026-10-15T18:05:00Z", status: "partial" })],
      "2026-10-15",
      hhmm,
    )?.text,
    "Sent 18:05",
  );
  // Set up, but not sent that day, or sent and failed: nothing to say on the tab.
  assert.equal(reportsBadge([report({ report_date: "2026-10-14" })], "2026-10-15", hhmm), null);
  assert.equal(reportsBadge([report({ status: "failed" })], "2026-10-15", hhmm), null);
  assert.equal(reportsBadge([report(null)], "2026-10-15", hhmm), null);
});

test("the pod bar lists the read's pods worst first, then the rest, and a counts-only reader the read's only", () => {
  const rows = [
    {
      id: "pod-a",
      name: "Alpha",
      done: 1,
      total: 4,
      verdict: "on_track" as const,
      target: null,
      p85: null,
    },
    {
      id: "pod-b",
      name: "Beta",
      done: 0,
      total: 3,
      verdict: "off_track" as const,
      target: null,
      p85: null,
    },
  ];
  const directory = [
    { id: "pod-a", name: "Alpha" },
    { id: "pod-c", name: "Gamma" },
  ];
  const mine = new Set(["pod-c"]);
  assert.deepEqual(
    podChoices(rows, directory, mine, false).map((pod) => [pod.id, pod.verdict, pod.own]),
    [
      ["pod-b", "off_track", false],
      ["pod-a", "on_track", false],
      ["pod-c", null, true],
    ],
  );
  assert.deepEqual(
    podChoices(rows, directory, mine, true).map((pod) => pod.id),
    ["pod-b", "pod-a"],
  );
  // A developer: their tree's pods, no read.
  assert.deepEqual(
    podChoices(null, directory, mine, false).map((pod) => pod.id),
    ["pod-a", "pod-c"],
  );
});

test("a pod's verdict chip, and every verdict's tone and words", () => {
  assert.deepEqual(podVerdict({ verdict: "at_risk", total: 3 }), {
    label: "At risk",
    tone: "warning",
  });
  assert.deepEqual(podVerdict({ verdict: "no_date", total: 3 }), {
    label: "No committed date",
    tone: "danger",
  });
  assert.deepEqual(podVerdict({ verdict: "on_track", total: 0 }), {
    label: "Nothing in scope",
    tone: "neutral",
  });
  assert.equal(podVerdict({ verdict: null, total: null }), null);
  assert.equal(verdictTone("off_track"), "danger");
  assert.equal(verdictTone("done"), "success");
  assert.equal(verdictTone(null), "neutral");
  assert.equal(verdictWords(null), "No forecast yet");
  assert.equal(verdictWords("not_enough_data"), "Not enough history to forecast");
});

test("a pod's badges read the pod's part of its project, a project's its own, All every project", () => {
  assert.deepEqual(badgeScope({ level: "all" }), { projectId: null, podId: null });
  assert.deepEqual(badgeScope({ level: "project", projectId: "p" }), {
    projectId: "p",
    podId: null,
  });
  assert.deepEqual(badgeScope({ level: "pod", projectId: "p", podId: "x" }), {
    projectId: "p",
    podId: "x",
  });
});

test("the scope badge sums every project at All, and reads the place's row below it", () => {
  const row = (id: string, added: number, open: number) =>
    ({ id, plan: { added }, hygiene_open: open }) as unknown as ScopeProjectDto;
  const read = { projects: [row("p", 2, 1), row("q", 1, 0)], findings_total: 4 };
  assert.deepEqual(scopeBadgeAt(null, read), {
    text: "+3 · 4",
    label: "3 added after planning, 4 open Jira hygiene findings",
  });
  // The pod's part of p: the read narrows the row to the pod.
  assert.deepEqual(scopeBadgeAt("p", { projects: [row("p", 1, 0)], findings_total: 0 }), {
    text: "+1",
    label: "1 added after planning",
  });
  assert.equal(scopeBadgeAt("r", read), null);
  assert.equal(scopeBadgeAt("p", undefined), null);
});

test("a counts-only reader opens a pod's level only for a listed pod of five or more", () => {
  assert.equal(opensPodLevel(0, { listed: false, people: null }), true);
  assert.equal(opensPodLevel(0, { listed: true, people: 2 }), true);
  assert.equal(opensPodLevel(5, { listed: true, people: 5 }), true);
  assert.equal(opensPodLevel(5, { listed: true, people: 4 }), false);
  assert.equal(opensPodLevel(5, { listed: false, people: 9 }), false);
  assert.equal(opensPodLevel(5, { listed: true, people: null }), false);
});
