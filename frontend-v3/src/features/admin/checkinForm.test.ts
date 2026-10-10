import assert from "node:assert/strict";
import { test } from "node:test";

import type { CheckinPreferenceResponse } from "../../api/schema";
import {
  askedDays,
  changesFrom,
  daysNote,
  draftFrom,
  draftProblems,
  editField,
  inheritedSummary,
  isTimeZone,
  secondsFromMinutes,
  sendDays,
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

// The backend's default send: 09:30 UTC, Monday to Friday.
const SEND: CheckinPreferenceResponse["send"] = {
  kind: "weekly",
  cron: "30 9 * * 1-5",
  timezone: "UTC",
  local_time: "09:30:00",
  weekdays: [0, 1, 2, 3, 4],
  month_days: null,
  months: null,
};
// qa2's paused schedule, 1 January only, and one with no one time.
const NEW_YEAR: CheckinPreferenceResponse["send"] = {
  ...SEND,
  kind: "dates",
  cron: "0 0 1 1 *",
  local_time: "00:00:00",
  weekdays: null,
  month_days: [1],
  months: [1],
};
const HOURLY: CheckinPreferenceResponse["send"] = {
  ...SEND,
  kind: "other",
  cron: "0 9-17 * * 1-5",
  local_time: null,
  weekdays: null,
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
  send: SEND,
};

test("only the days the bot sends on are offered, and a stored weekend is not shown as asked", () => {
  assert.deepEqual(sendDays(SET), [0, 1, 2, 3, 4]);
  assert.deepEqual(askedDays([6, 0, 5, 2], SET), [0, 2]);
  assert.equal(daysNote(SET), null);
  const everyDay: CheckinPreferenceResponse = { ...SET, weekdays: [0, 1, 2, 3, 4, 5, 6] };
  const draft = draftFrom(everyDay);
  assert.deepEqual(draft.weekdays, [0, 1, 2, 3, 4]);
  // Untouched, nothing is sent; an edit sends only days the bot sends on.
  assert.deepEqual(changesFrom(everyDay, draft), {});
  assert.deepEqual(changesFrom(everyDay, editField(draft, everyDay, "weekdays", [0, 1, 2, 3])), {
    weekdays: [0, 1, 2, 3],
  });
});

test("off a weekly schedule every day is offered, and the note says a day only skips a send", () => {
  for (const send of [NEW_YEAR, HOURLY]) {
    const everyDay: CheckinPreferenceResponse = { ...SET, weekdays: [0, 1, 2, 3, 4, 5, 6], send };
    assert.deepEqual(sendDays(everyDay), [0, 1, 2, 3, 4, 5, 6]);
    assert.deepEqual(askedDays([6, 0, 5], everyDay), [0, 5, 6]);
    assert.equal(
      daysNote(everyDay),
      "Check-ins aren't on a weekly schedule, so these days only skip a send that falls on a day left off.",
    );
    const draft = draftFrom(everyDay);
    assert.deepEqual(draft.weekdays, [0, 1, 2, 3, 4, 5, 6]);
    assert.deepEqual(changesFrom(everyDay, draft), {});
    assert.deepEqual(
      changesFrom(everyDay, editField(draft, everyDay, "weekdays", [0, 1, 2, 3, 4, 5])),
      { weekdays: [0, 1, 2, 3, 4, 5] },
    );
  }
});

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
