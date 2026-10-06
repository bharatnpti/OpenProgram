import assert from "node:assert/strict";
import { test } from "node:test";

import type { ReportRunResponse } from "../../api/schema";
import {
  destinationsSummary,
  emailDestinations,
  emptyReportForm,
  formProblems,
  requestFromForm,
  runStartedBy,
  scheduleLabel,
} from "./reportForm.ts";

test("a schedule reads as a sentence", () => {
  assert.equal(
    scheduleLabel("18:00:00", "Europe/Berlin", [4, 0, 1, 2, 3]),
    "Mon–Fri at 18:00 (Europe/Berlin)",
  );
  assert.equal(scheduleLabel("09:30", "UTC", [0, 2]), "Mon, Wed at 09:30 (UTC)");
  assert.equal(scheduleLabel("09:30", "UTC", [0, 1, 2, 3, 4, 5, 6]), "Every day at 09:30 (UTC)");
});

test("emails split on any separator, lower-cased, without repeats", () => {
  assert.deepEqual(emailDestinations("Team@Example.com, ops@example.com\nteam@example.com; nope"), [
    { kind: "email", target: "team@example.com" },
    { kind: "email", target: "ops@example.com" },
  ]);
});

test("a report that is on needs a destination; teams needs no target", () => {
  const form = { ...emptyReportForm("UTC"), name: "Daily", projectId: "checkout" };

  assert.deepEqual(formProblems(form), ["Add somewhere for it to go, or switch it off."]);
  assert.deepEqual(formProblems({ ...form, enabled: false }), []);
  const teams = { ...form, destinations: [{ kind: "teams" as const, target: "" }] };
  assert.deepEqual(formProblems(teams), []);
  assert.equal(requestFromForm(teams).schedule.local_time, "18:00:00");
  assert.equal(requestFromForm(teams).release_id, null);
  assert.equal(requestFromForm({ ...teams, releaseId: "rel-1" }).release_id, "rel-1");
});

test("a summary counts each kind of destination", () => {
  assert.equal(
    destinationsSummary([
      { kind: "email", target: "a@x.io" },
      { kind: "email", target: "b@x.io" },
      { kind: "person", target: "dev-1" },
      { kind: "teams", target: "" },
    ]),
    "2 email addresses, 1 person, the Teams channel",
  );
  assert.equal(destinationsSummary([]), "Nowhere yet");
});

test("a send names who sent it, else shows the id, and a scheduled one says so", () => {
  const run: ReportRunResponse = {
    run_id: "r1",
    report_id: "rep-1",
    report_date: "2026-10-06",
    trigger: "manual",
    status: "sent",
    started_at: "2026-10-06T16:00:00Z",
    finished_at: "2026-10-06T16:00:02Z",
    title: "Checkout: day report",
    outcomes: [],
    actor: "U0C1",
    actor_name: "Asha Rao",
  };
  assert.equal(runStartedBy(run), "sent by Asha Rao");
  assert.equal(runStartedBy({ ...run, actor_name: null }), "sent by U0C1");
  assert.equal(runStartedBy({ ...run, actor: null, actor_name: null }), "sent by an admin");
  assert.equal(runStartedBy({ ...run, trigger: "schedule", actor: null }), "on schedule");
});
