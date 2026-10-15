import assert from "node:assert/strict";
import { test } from "node:test";

import type { AppRole } from "../../app/roleWords.ts";
import { podPlanLine, podReads, podScopeLine } from "./podLevel.ts";

const reads = (lens: AppRole[], own = false) => podReads(lens, own);

test("a manager and an admin read every pod's part whole", () => {
  for (const role of ["mgr", "admin"] as const) {
    assert.deepEqual(reads([role]), {
      morning: true,
      warnings: true,
      projectTimeline: true,
      requirements: true,
      scope: true,
      readiness: true,
      reports: true,
      flow: true,
      trend: true,
    });
  }
});

test("a scrum master reads their own pods whole and the others' warnings and dates", () => {
  const own = reads(["sm"], true);
  const other = reads(["sm"], false);
  assert.equal(own.morning && own.scope && own.readiness, true);
  assert.equal(own.requirements, false);
  assert.deepEqual(
    { morning: other.morning, scope: other.scope, readiness: other.readiness },
    { morning: false, scope: false, readiness: false },
  );
  assert.equal(other.warnings && other.projectTimeline && other.reports, true);
  assert.equal(own.trend && other.trend, true);
});

test("a product owner and an executive read no pod's morning", () => {
  for (const role of ["po", "exec"] as const) {
    const part = reads([role], true);
    assert.equal(part.morning, false);
    assert.equal(part.readiness, false);
    assert.equal(part.warnings && part.requirements && part.scope && part.reports, true);
    assert.equal(part.trend, true);
  }
});

test("a developer reads their own pod's morning and warnings, and no other pod", () => {
  const own = reads(["dev"], true);
  assert.equal(own.morning && own.warnings, true);
  assert.equal(
    own.projectTimeline || own.requirements || own.scope || own.reports || own.flow || own.trend,
    false,
  );
  assert.equal(reads(["dev"], false).warnings, false);
  assert.equal(reads(["dev"], false).morning, false);
});

test("the pod's part of the plan in one line", () => {
  const plan = {
    planned_on: "2026-05-26",
    planned: 4,
    added: 1,
    without_label: 1,
    without_label_percent: 100,
    change_label: "CR",
    removed: 0,
    added_days: null,
  };
  assert.equal(
    podPlanLine(plan),
    "4 requirements planned on Tue 26 May · 1 added after, 1 without the CR label · none removed",
  );
  assert.equal(
    podPlanLine({ ...plan, planned: 1, added: 0, without_label: 0, removed: 2 }),
    "1 requirement planned on Tue 26 May · nothing added after · 2 removed",
  );
});

test("the pod's Scope control line names only the sections drawn", () => {
  assert.equal(
    podScopeLine("Payments Pod", ["baseline", "gates", "readiness", "hygiene"]),
    "Payments Pod's part of the plan, acceptance gates, readiness before production and Jira hygiene.",
  );
  assert.equal(
    podScopeLine("Storefront Pod", ["gates", "hygiene"]),
    "Storefront Pod's acceptance gates and Jira hygiene.",
  );
  assert.equal(podScopeLine("Data Pod", ["gates"]), "Data Pod's acceptance gates.");
});
