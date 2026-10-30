import assert from "node:assert/strict";
import test from "node:test";

import type { CheckinDeveloperDto } from "../../api/schema";
import {
  colourReasons,
  defaultPod,
  ladderLine,
  nudgeColumns,
  nudgeState,
  podLine,
  podOptions,
  recentPodsKey,
  rungWords,
  scheduleWords,
  scrumMasterOf,
} from "./podWords.ts";

const time = (iso: string) => iso.slice(11, 16);
const person = (over: Partial<CheckinDeveloperDto>): CheckinDeveloperDto => ({
  developer_id: "U1",
  developer_name: "Noah",
  state: "missing",
  source: "unknown",
  status_as_of: null,
  summary: "",
  evidence: [],
  no_data_cause: null,
  asked_at: null,
  replied_at: null,
  reminders: [],
  checkin_time: null,
  timezone: null,
  ...over,
});

test("a rung says whom it reached and when, and when its target is today's ladder's", () => {
  assert.equal(
    rungWords(
      { number: 1, target: "developer", sent_at: "2026-10-13T13:30:00Z", recorded: true },
      time,
    ),
    "Reminder sent 13:30",
  );
  assert.equal(
    rungWords(
      { number: 2, target: "scrum_master", sent_at: "2026-10-13T17:30:00Z", recorded: false },
      time,
    ),
    "Scrum master told 17:30 (by today's ladder)",
  );
  assert.equal(
    rungWords({ number: 4, target: null, sent_at: "2026-10-13T18:00:00Z", recorded: false }, time),
    "Rung 4 sent 18:00 (by today's ladder)",
  );
});

test("the ladder line is the ask and the rungs that went out, in order", () => {
  const silent = person({
    asked_at: "2026-10-13T09:30:00Z",
    reminders: [
      { number: 1, target: "developer", sent_at: "2026-10-13T13:30:00Z", recorded: true },
      { number: 2, target: "scrum_master", sent_at: "2026-10-13T17:30:00Z", recorded: true },
    ],
  });
  assert.equal(
    ladderLine(silent, time),
    "Asked 09:30 · Reminder sent 13:30 · Scrum master told 17:30",
  );
  assert.deepEqual(nudgeState(silent, "2026-10-13", time), {
    word: "No reply yet",
    tone: "neutral",
    silent: true,
  });
});

test("a reply says when; a day filled from Jira is never green", () => {
  const replied = person({
    state: "confirmed",
    source: "confirmed",
    status_as_of: "2026-10-13",
    replied_at: "2026-10-13T09:41:00Z",
  });
  assert.equal(nudgeState(replied, "2026-10-13", time).word, "Replied 09:41");
  assert.equal(nudgeState(replied, "2026-10-13", time).tone, "success");
  const filled = person({ state: "stale", source: "inferred", status_as_of: "2026-10-13" });
  assert.deepEqual(nudgeState(filled, "2026-10-13", time), {
    word: "Filled from Jira",
    tone: "warning",
    silent: true,
  });
  assert.equal(nudgeState(person({}), "2026-10-13", time).word, "Not asked yet");
});

test("a check-in time reads with its zone", () => {
  assert.equal(
    scheduleWords({ checkin_time: "09:30", timezone: "Europe/Berlin" }),
    "09:30 Europe/Berlin",
  );
  assert.equal(scheduleWords({ checkin_time: null, timezone: "UTC" }), null);
});

test("the pod's line says each part only when it is known", () => {
  assert.equal(
    podLine({
      scrumMaster: "Ira Novak",
      members: 6,
      replied: 4,
      counted: 6,
      day: "today",
      blockers: 2,
    }),
    "Scrum master Ira Novak · 6 members · 4 of 6 replied today · 2 open blockers",
  );
  // Counted as the boards count them: a partial reply is said beside the replies.
  assert.equal(
    podLine({
      scrumMaster: null,
      members: 6,
      replied: 2,
      partly: 1,
      counted: 6,
      day: "today",
      blockers: 0,
    }),
    "6 members · 2 of 6 replied today · 1 partly · 0 open blockers",
  );
  assert.equal(
    podLine({
      scrumMaster: null,
      members: 1,
      replied: null,
      counted: null,
      day: "today",
      blockers: null,
    }),
    "1 member",
  );
});

test("the scrum master is the member sm_id names, else the escalation contact", () => {
  assert.equal(
    scrumMasterOf({
      metadata: {},
      people: [{ key: "sm_id", id: "U1006", member_id: "U1006", name: "Ira Novak" }],
    }),
    "Ira Novak",
  );
  assert.equal(
    scrumMasterOf({ metadata: { escalation_sm_display_name: "Ira" }, people: [] }),
    "Ira",
  );
  assert.equal(scrumMasterOf({ metadata: {}, people: [] }), null);
});

test("a bare /pods opens the worst of mine, else the last opened, else the worst", () => {
  const pods = [
    { id: "a", name: "Alpha", rag: "green" as const, project_ids: ["p"] },
    { id: "b", name: "Beta", rag: "red" as const, project_ids: ["p"] },
    { id: "c", name: "Gamma", rag: "amber" as const, project_ids: ["q"] },
  ];
  assert.equal(defaultPod(podOptions(pods, new Set(["a", "c"])), []), "c");
  assert.equal(defaultPod(podOptions(pods, new Set()), ["a"]), "a");
  assert.equal(defaultPod(podOptions(pods, new Set()), ["gone"]), "b");
  assert.equal(defaultPod([], []), null);
  assert.equal(podOptions(pods, new Set(["a"]))[0].programIds[0], "p");
  assert.equal(recentPodsKey("U1006"), "openprogram.v3.recent-pods.U1006");
});

test("Why this colour leaves the blockers to Blockers and waits", () => {
  const factor = (kind: string, description: string) =>
    ({
      kind,
      description,
      contributes: "amber",
      source_ref: { tenant_id: "t", kind: "pod", id: "pod-1" },
      blocker_id: null,
      work_item_ref: null,
      unattributed: false,
      applies_to_pod_ids: [],
    }) as never;
  const kept = colourReasons([
    factor("blocker", "Blocker: waiting on the vendor"),
    factor("status", "2 not replied."),
    factor("task", "Task CHK-1 needs attention."),
  ]);
  assert.deepEqual(
    kept.map((item: { description: string }) => item.description),
    ["2 not replied.", "Task CHK-1 needs attention."],
  );
});

test("who needs a nudge adds the check-in time for a schedule reader and Ask for an admin", () => {
  assert.deepEqual(nudgeColumns({ schedule: false, askNow: false }), [
    "Person",
    "Check-in",
    "Asked and reminded",
  ]);
  assert.deepEqual(nudgeColumns({ schedule: true, askNow: true }), [
    "Person",
    "Check-in",
    "Asked and reminded",
    "Their check-in time",
    "Ask for a check-in",
  ]);
});
