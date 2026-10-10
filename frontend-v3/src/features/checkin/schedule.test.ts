import assert from "node:assert/strict";
import { test } from "node:test";

import type { CheckinPreferenceResponse } from "../../api/schema";
import {
  askTimeWords,
  changeLines,
  clockTime,
  draftFrom,
  noCheckinWords,
  refusalWords,
  scheduleChanges,
  scheduleProblem,
  scheduleSummary,
  timezoneOptions,
  toggleDay,
  waitWords,
} from "./schedule.ts";

const preference = (
  overrides: Partial<CheckinPreferenceResponse> = {},
): CheckinPreferenceResponse => ({
  developer_id: "U1007",
  local_time: "09:30:00",
  timezone: "Europe/Berlin",
  weekdays: [0, 1, 2, 3, 4],
  reply_wait_seconds: 14400,
  final_reply_wait_seconds: 28800,
  inherited: [],
  defaults: {
    local_time: "09:30:00",
    timezone: "UTC",
    weekdays: [0, 1, 2, 3, 4],
    reply_wait_seconds: 14400,
    final_reply_wait_seconds: 28800,
  },
  ...overrides,
});

test("a value the person never set follows the team, and says so", () => {
  const draft = draftFrom(preference({ inherited: ["weekdays", "timezone"], timezone: "UTC" }));
  assert.deepEqual(draft, { teamDays: true, weekdays: [0, 1, 2, 3, 4], timezone: null });
  assert.equal(
    scheduleSummary(preference({ inherited: ["timezone"], timezone: "UTC" })),
    "Mon–Fri · team time zone (UTC)",
  );
  assert.equal(scheduleSummary(preference()), "Mon–Fri · Europe/Berlin");
});

test("nothing changed sends nothing", () => {
  const initial = draftFrom(preference());
  assert.deepEqual(scheduleChanges(initial, { ...initial }), {});
  assert.deepEqual(
    scheduleChanges(initial, { ...initial, weekdays: [4, 3, 2, 1, 0] }),
    {},
    "the same days in another order are no change",
  );
});

test("only the changed field is sent", () => {
  const initial = draftFrom(preference());
  assert.deepEqual(
    scheduleChanges(initial, { ...initial, weekdays: toggleDay(initial.weekdays, 2) }),
    {
      weekdays: [0, 1, 3, 4],
    },
  );
  assert.deepEqual(scheduleChanges(initial, { ...initial, timezone: "Asia/Kolkata" }), {
    timezone: "Asia/Kolkata",
  });
});

test("going back to the team sends null, which the backend reads as the team default", () => {
  const initial = draftFrom(preference());
  assert.deepEqual(scheduleChanges(initial, { ...initial, teamDays: true }), { weekdays: null });
  assert.deepEqual(scheduleChanges(initial, { ...initial, timezone: null }), { timezone: null });
  const following = draftFrom(preference({ inherited: ["weekdays"] }));
  assert.deepEqual(
    scheduleChanges(following, { ...following, teamDays: false }),
    { weekdays: [0, 1, 2, 3, 4] },
    "choosing one's own days pins them, even when they match the team's",
  );
});

test("no days at all is refused before the backend refuses it", () => {
  const initial = draftFrom(preference());
  assert.match(scheduleProblem({ ...initial, weekdays: [] }) ?? "", /at least one day/);
  assert.equal(scheduleProblem({ ...initial, teamDays: true, weekdays: [] }), null);
  assert.equal(scheduleProblem(initial), null);
});

test("the summary of a change names the team's values", () => {
  const initial = draftFrom(preference());
  assert.deepEqual(changeLines(initial, { ...initial, teamDays: true, timezone: null }, "UTC"), [
    "Days: Mon–Fri → your team's (Mon–Fri)",
    "Time zone: Europe/Berlin → your team's (UTC)",
  ]);
  assert.deepEqual(changeLines(initial, initial, "UTC"), []);
});

test("the zone list keeps the stored zone even when the browser lacks it", () => {
  const zones = timezoneOptions("Mars/Olympus_Mons", null);
  assert.ok(zones.includes("Mars/Olympus_Mons"));
  assert.ok(zones.includes("UTC"));
  assert.deepEqual(zones, [...zones].sort());
});

test("why nobody asks: no member record, or a sign-in with no role", () => {
  assert.equal(
    noCheckinWords(404, "not available"),
    "The bot doesn't ask you: you have no member record.",
  );
  assert.match(
    noCheckinWords(403, "role scope does not include this view"),
    /no role.*The server said: role scope/,
  );
});

test("reply windows read as hours and minutes", () => {
  assert.equal(waitWords(14400), "4 hours");
  assert.equal(waitWords(3600), "1 hour");
  assert.equal(waitWords(5400), "1 hour 30 minutes");
  assert.equal(waitWords(2700), "45 minutes");
});

test("a refused save says why in the backend's own words", () => {
  assert.equal(
    refusalWords({
      detail: [
        { msg: "Value error, timezone must be a valid IANA timezone", loc: ["body", "timezone"] },
      ],
    }),
    "timezone must be a valid IANA timezone",
  );
  assert.equal(
    refusalWords({ detail: "check-in preference is not available" }),
    "check-in preference is not available",
  );
  assert.equal(refusalWords({ detail: [] }), null);
  assert.equal(refusalWords(null), null);
});

test("the dialog reads out the time the bot asks, though it is not the person's to set", () => {
  assert.equal(clockTime("09:30:00"), "09:30");
  assert.match(
    askTimeWords(preference({ timezone: "Asia/Kolkata" })),
    /^The bot asks you at 09:30 Asia\/Kolkata time, the same time for your whole team/,
  );
  // A person who follows the team's zone is told the team's.
  assert.match(askTimeWords(preference({ timezone: null })), /at 09:30 UTC time/);
});

test("the zone list offers only names a current server knows", () => {
  const zones = timezoneOptions("Asia/Kolkata", "Europe/Berlin");
  assert.ok(!zones.includes("Asia/Calcutta"));
  assert.ok(!zones.includes("Europe/Kiev"));
  assert.ok(zones.includes("Asia/Kolkata"));
});
