import assert from "node:assert/strict";
import { test } from "node:test";

import type { CheckinPreferenceResponse, CheckinSendResponse } from "../../api/schema";
import {
  askTimeWords,
  askedDays,
  changeLines,
  clockTime,
  draftFrom,
  nextSendAt,
  noCheckinWords,
  refusalWords,
  scheduleChanges,
  scheduleProblem,
  scheduleSummary,
  sendDays,
  sendTimeWords,
  sendWords,
  timezoneOptions,
  toggleDay,
  waitWords,
  zoneEffectWords,
} from "./schedule.ts";

// The backend's default: one send at 09:30 UTC, Monday to Friday.
const SEND: CheckinSendResponse = {
  cron: "30 9 * * 1-5",
  timezone: "UTC",
  local_time: "09:30:00",
  weekdays: [0, 1, 2, 3, 4],
};

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
  send: SEND,
  ...overrides,
});

// Friday 9 October 2026, 08:00 UTC (summer time in Berlin, UTC+2), and a
// winter Monday, 7 December 2026, 08:00 UTC (UTC+1).
const FRIDAY_SUMMER = new Date("2026-10-09T08:00:00Z");
const MONDAY_WINTER = new Date("2026-12-07T08:00:00Z");

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

test("the dialog names the one send in UTC and on the person's own clock, never their stored time", () => {
  assert.equal(clockTime("09:30:00"), "09:30");
  // Liam's own row says 07:00 Europe/Berlin; the bot still asks everyone at 09:30 UTC.
  assert.equal(
    askTimeWords(SEND, "Europe/Berlin", FRIDAY_SUMMER),
    "The bot asks everyone at 09:30 UTC (11:30 in Europe/Berlin), Mon–Fri: one time for the whole team, so it isn't yours to set. You choose which of those days it asks you, and your time zone.",
  );
  assert.match(
    askTimeWords(SEND, "UTC", FRIDAY_SUMMER),
    /^The bot asks everyone at 09:30 UTC, Mon–Fri:/,
  );
  assert.match(
    askTimeWords(SEND, null, FRIDAY_SUMMER),
    /^The bot asks everyone at 09:30 UTC, Mon–Fri:/,
  );
});

test("the clock in another zone is the next send's, so summer time and the date line show", () => {
  assert.equal(
    sendTimeWords(SEND, "Europe/Berlin", FRIDAY_SUMMER),
    "09:30 UTC (11:30 in Europe/Berlin)",
  );
  assert.equal(
    sendTimeWords(SEND, "Europe/Berlin", MONDAY_WINTER),
    "09:30 UTC (10:30 in Europe/Berlin)",
  );
  assert.equal(
    sendTimeWords(SEND, "Asia/Kolkata", FRIDAY_SUMMER),
    "09:30 UTC (15:00 in Asia/Kolkata)",
  );
  assert.equal(
    sendTimeWords(SEND, "Pacific/Honolulu", FRIDAY_SUMMER),
    "09:30 UTC (23:30 the day before in Pacific/Honolulu)",
  );
  // The same clock in a zone that is UTC in winter is not said twice.
  assert.equal(sendTimeWords(SEND, "Europe/London", MONDAY_WINTER), "09:30 UTC");
  // A zone this browser doesn't know: UTC only.
  assert.equal(sendTimeWords(SEND, "Mars/Olympus_Mons", FRIDAY_SUMMER), "09:30 UTC");
});

test("the next send skips the days the bot doesn't send on", () => {
  // Friday 08:00: today's 09:30 is still to come.
  assert.equal(nextSendAt(SEND, FRIDAY_SUMMER)?.toISOString(), "2026-10-09T09:30:00.000Z");
  // Friday 10:00: the next is Monday.
  assert.equal(
    nextSendAt(SEND, new Date("2026-10-09T10:00:00Z"))?.toISOString(),
    "2026-10-12T09:30:00.000Z",
  );
  assert.equal(nextSendAt({ ...SEND, local_time: null }, FRIDAY_SUMMER), null);
});

test("a schedule the backend can't read as one time says its cron, and in UTC", () => {
  const several = {
    cron: "0 9,15 * * 1-5",
    timezone: "UTC",
    local_time: null,
    weekdays: [0, 1, 2, 3, 4],
  };
  assert.equal(
    sendWords(several, "Europe/Berlin", FRIDAY_SUMMER),
    "on the schedule 0 9,15 * * 1-5 (UTC), Mon–Fri",
  );
  const monthly = { cron: "30 9 1 * *", timezone: "UTC", local_time: "09:30:00", weekdays: null };
  assert.equal(sendWords(monthly, "UTC", FRIDAY_SUMMER), "at 09:30 UTC");
});

test("the time zone says what it changes, and that the send time isn't one of those things", () => {
  assert.equal(
    zoneEffectWords(SEND),
    "Decides which day your reply counts for. It doesn't change when the bot asks you: that is 09:30 UTC for everyone.",
  );
});

test("only the days the bot sends on are offered or shown as asked", () => {
  assert.deepEqual(sendDays(SEND), [0, 1, 2, 3, 4]);
  assert.deepEqual(sendDays({ weekdays: null }), [0, 1, 2, 3, 4, 5, 6]);
  // Liam chose every day; the bot sends Monday to Friday, so that is when he is asked.
  assert.deepEqual(askedDays([0, 1, 2, 3, 4, 5, 6], SEND), [0, 1, 2, 3, 4]);
  assert.deepEqual(askedDays([4, 5, 0], SEND), [0, 4]);
  const everyDay = preference({ weekdays: [0, 1, 2, 3, 4, 5, 6] });
  assert.equal(scheduleSummary(everyDay), "Mon–Fri · Europe/Berlin");
  const initial = draftFrom(everyDay);
  assert.deepEqual(initial.weekdays, [0, 1, 2, 3, 4]);
  // Opening and saving changes nothing, so a stored Saturday is not rewritten by a look.
  assert.deepEqual(scheduleChanges(initial, { ...initial }), {});
  // Leaving out Wednesday sends the days the bot sends on, without the unused weekend.
  assert.deepEqual(
    scheduleChanges(initial, { ...initial, weekdays: toggleDay(initial.weekdays, 2) }),
    {
      weekdays: [0, 1, 3, 4],
    },
  );
});

test("the zone list offers only names a current server knows", () => {
  const zones = timezoneOptions("Asia/Kolkata", "Europe/Berlin");
  assert.ok(!zones.includes("Asia/Calcutta"));
  assert.ok(!zones.includes("Europe/Kiev"));
  assert.ok(zones.includes("Asia/Kolkata"));
});
