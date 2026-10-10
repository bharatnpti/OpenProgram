import assert from "node:assert/strict";
import { test } from "node:test";

import {
  cronLabel,
  errorText,
  minutesLabel,
  nextCronRun,
  peopleCount,
  providerLabel,
  slugId,
  stampLabel,
  targetParts,
} from "./adminWords.ts";

// Each test file runs in its own process; pin the zone so local times are known.
process.env.TZ = "Asia/Kolkata";

test("a timestamp reads with its local date, not the UTC one", () => {
  // 18:37 UTC on the 6th is 00:07 on the 7th in India.
  assert.equal(stampLabel("2026-10-06T18:37:00Z"), "Wed 7 Oct 00:07");
  assert.equal(stampLabel(null), "never");
  assert.equal(stampLabel("not a time"), "—");
});

test("a wait reads in the largest whole unit", () => {
  assert.equal(minutesLabel(7200), "2 h");
  assert.equal(minutesLabel(5400), "90 min");
  assert.equal(minutesLabel(86400), "1 day");
  assert.equal(minutesLabel(172800), "2 days");
  assert.equal(minutesLabel(0), "0 min");
});

test("a new entity gets a readable, stable id", () => {
  assert.equal(slugId("project", "Checkout Revamp"), "project-checkout-revamp");
  assert.equal(slugId("pod", "  Café & Data!! "), "pod-cafe-data");
  assert.equal(slugId("ws", "???"), "ws-new");
});

test("a refused request says each problem the server named", () => {
  const unreadable = {
    message: "Request failed with status 422.",
    detail: {
      detail: [
        {
          type: "value_error",
          loc: ["body", "timezone"],
          msg: "Value error, timezone must be a valid IANA timezone",
        },
        { type: "missing", loc: ["body", "levels", 0, "label"], msg: "Field required" },
      ],
    },
  };
  assert.equal(
    errorText(unreadable),
    "Timezone must be a valid IANA timezone. Label: Field required.",
  );
  assert.equal(
    errorText({ message: "Fill in API token before turning Jira on.", detail: { detail: "x" } }),
    "Fill in API token before turning Jira on.",
  );
  assert.equal(errorText(new Error("Could not reach the server.")), "Could not reach the server.");
  assert.equal(errorText(undefined), "Something went wrong.");
});

test("a sync schedule reads in words", () => {
  assert.equal(cronLabel("*/15 * * * *"), "every 15 minutes");
  assert.equal(cronLabel("0 * * * *"), "every hour");
  assert.equal(cronLabel("0 */6 * * *"), "every 6 hours");
  assert.equal(cronLabel("0 8 * * *"), "once a day at 08:00 UTC");
  assert.equal(cronLabel("30 9 * * 1-5"), "on weekdays at 09:30 UTC");
  assert.equal(cronLabel("0 0 1 * *"), "on the schedule 0 0 1 * *");
  assert.equal(cronLabel(null), "on no schedule");
});

test("the next run of a schedule is found in UTC", () => {
  const from = new Date("2026-10-06T18:08:49Z");
  assert.equal(nextCronRun("0 * * * *", from)?.toISOString(), "2026-10-06T19:00:00.000Z");
  assert.equal(nextCronRun("*/15 * * * *", from)?.toISOString(), "2026-10-06T18:15:00.000Z");
  assert.equal(nextCronRun("0 */6 * * *", from)?.toISOString(), "2026-10-07T00:00:00.000Z");
  // Tuesday evening: the next weekday run is Wednesday morning.
  assert.equal(nextCronRun("30 9 * * 1-5", from)?.toISOString(), "2026-10-07T09:30:00.000Z");
  // A run exactly now is not "next".
  assert.equal(
    nextCronRun("0 * * * *", new Date("2026-10-06T19:00:00Z"))?.toISOString(),
    "2026-10-06T20:00:00.000Z",
  );
  assert.equal(nextCronRun("0 0 1 * *", from), null);
  assert.equal(nextCronRun("not a cron", from), null);
});

test("a stand-in provider says it is simulated; a real one is named", () => {
  assert.equal(providerLabel("fake", true), "simulated");
  assert.equal(providerLabel("mock_slack", true), "simulated");
  assert.equal(providerLabel("gitlab", false), "GitLab");
  assert.equal(providerLabel("jira", false), "Jira");
  assert.equal(providerLabel("acme_tracker", false), "acme_tracker");
});

test("a sync target names its kind once and says what it covers", () => {
  assert.deepEqual(
    targetParts({
      scope: "query:pod:pod-payments:8790955b8c86",
      label: "Pod Payments Pod",
      detail: 'project = "CHK"',
    }),
    { name: "Payments Pod", kind: "Pod, by saved query", detail: 'project = "CHK"' },
  );
  assert.deepEqual(
    targetParts({ scope: "query:project:project-checkout:87", label: "Project Checkout Revamp" }),
    { name: "Checkout Revamp", kind: "Project, by saved query", detail: null },
  );
  assert.deepEqual(targetParts({ scope: "project:CHK", label: "Project Checkout Revamp" }), {
    name: "Checkout Revamp",
    kind: "Jira project",
    detail: "Key CHK",
  });
  assert.deepEqual(targetParts({ scope: "project:CHK", label: "Project CHK" }), {
    name: "CHK",
    kind: "Jira project",
    detail: null,
  });
  assert.deepEqual(
    targetParts({
      scope: "repo:acme/checkout-api",
      label: "acme/checkout-api",
      detail: "Linked to Checkout Revamp",
    }),
    { name: "acme/checkout-api", kind: "Repository", detail: "Linked to Checkout Revamp" },
  );
  assert.deepEqual(targetParts({ scope: "workspace", label: "Chat workspace members" }), {
    name: "Chat workspace members",
    kind: "Workspace",
    detail: null,
  });
});

test("people are counted in words", () => {
  assert.equal(peopleCount(1), "1 person");
  assert.equal(peopleCount(14), "14 people");
});
