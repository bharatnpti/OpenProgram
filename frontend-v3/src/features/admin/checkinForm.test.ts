import assert from "node:assert/strict";
import { test } from "node:test";

import type { CheckinPreferenceResponse } from "../../api/schema";
import {
  changesFrom,
  draftFrom,
  draftProblems,
  editField,
  inheritedSummary,
  isTimeZone,
  secondsFromMinutes,
  toDefault,
  toEveryDefault,
} from "./checkinForm.ts";

const DEFAULTS = {
  local_time: "09:30:00",
  timezone: "UTC",
  weekdays: [0, 1, 2, 3, 4],
  reply_wait_seconds: 14400,
  final_reply_wait_seconds: 28800,
};

// As the seeded demo tenant has every member: each field set, even where it equals the default.
const SET: CheckinPreferenceResponse = {
  developer_id: "U1007",
  local_time: "09:30:00",
  timezone: "Europe/Berlin",
  weekdays: [0, 1, 2, 3, 4],
  reply_wait_seconds: 14400,
  final_reply_wait_seconds: 28800,
  inherited: [],
  defaults: DEFAULTS,
};

const FOLLOWING: CheckinPreferenceResponse = {
  ...SET,
  timezone: null,
  inherited: [
    "local_time",
    "timezone",
    "weekdays",
    "reply_wait_seconds",
    "final_reply_wait_seconds",
  ],
};

test("a save with nothing touched sends nothing", () => {
  assert.deepEqual(changesFrom(SET, draftFrom(SET)), {});
  assert.deepEqual(changesFrom(FOLLOWING, draftFrom(FOLLOWING)), {});
});

test("putting a field back to the team default sends null, and only for that field", () => {
  const draft = toDefault(draftFrom(SET), SET, "timezone");
  assert.equal(draft.timezone, "UTC");
  assert.deepEqual(changesFrom(SET, draft), { timezone: null });

  const all = toEveryDefault(draftFrom(SET), SET);
  assert.deepEqual(changesFrom(SET, all), {
    weekdays: null,
    timezone: null,
    reply_wait_seconds: null,
    final_reply_wait_seconds: null,
  });
});

test("an edit sets the field, in seconds", () => {
  const draft = editField(draftFrom(SET), SET, "reply_wait_seconds", "90");
  assert.deepEqual(changesFrom(SET, draft), { reply_wait_seconds: 5400 });
  assert.equal(draft.inherited.includes("reply_wait_seconds"), false);
});

test("setting a followed field to the default's value keeps it following", () => {
  let draft = editField(draftFrom(FOLLOWING), FOLLOWING, "weekdays", [0, 1, 2]);
  assert.deepEqual(changesFrom(FOLLOWING, draft), { weekdays: [0, 1, 2] });
  draft = editField(draft, FOLLOWING, "weekdays", [4, 3, 2, 1, 0]);
  assert.deepEqual(changesFrom(FOLLOWING, draft), {});
  assert.ok(draft.inherited.includes("weekdays"));
});

test("a set field changed back to what is stored sends nothing", () => {
  let draft = editField(draftFrom(SET), SET, "timezone", "Asia/Tokyo");
  draft = editField(draft, SET, "timezone", "Europe/Berlin");
  assert.deepEqual(changesFrom(SET, draft), {});
});

test("a wait that is not whole seconds survives an untouched save", () => {
  const odd = { ...SET, reply_wait_seconds: 90 };
  const draft = draftFrom(odd);
  assert.equal(draft.nudgeMinutes, "1.5");
  assert.deepEqual(changesFrom(odd, draft), {});
  assert.deepEqual(draftProblems(draft), []);
});

test("the form says what the server would refuse", () => {
  const draft = {
    ...draftFrom(SET),
    weekdays: [],
    timezone: "Europe/Berln",
    nudgeMinutes: "soon",
  };
  assert.deepEqual(draftProblems(draft), [
    "Pick at least one day: with none, their check-ins would stop without a word.",
    "“Europe/Berln” is not a time zone. Pick one from the list.",
    "Nudge after is a number of minutes, such as 240.",
  ]);
  assert.equal(isTimeZone("Asia/Kolkata"), true);
  assert.equal(isTimeZone(""), false);
  assert.equal(secondsFromMinutes("-5"), null);
  assert.equal(secondsFromMinutes("240"), 14400);
});

test("a member's row says which values follow the team", () => {
  assert.equal(inheritedSummary([]), "Every value set for this member");
  assert.equal(inheritedSummary(FOLLOWING.inherited), "Follows every team default");
  assert.equal(
    inheritedSummary(["local_time", "weekdays", "final_reply_wait_seconds"]),
    "Follows the team default for days and give-up wait",
  );
});
