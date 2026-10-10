import assert from "node:assert/strict";
import { test } from "node:test";

import type { CheckinPreferenceResponse, CheckinSendResponse } from "../../api/schema";
import {
  adminSendWords,
  askTimeWords,
  askedDays,
  changeLines,
  clockTime,
  datesWords,
  daysHintWords,
  daysLabel,
  daysLegendWords,
  daysWords,
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

const NO_DATES = { month_days: null, months: null };
// The backend's default: one send at 09:30 UTC, Monday to Friday.
const SEND: CheckinSendResponse = {
  kind: "weekly",
  cron: "30 9 * * 1-5",
  timezone: "UTC",
  local_time: "09:30:00",
  weekdays: [0, 1, 2, 3, 4],
  ...NO_DATES,
};
// 09:30 UTC on all seven days: the one schedule that is every day.
const DAILY: CheckinSendResponse = {
  ...SEND,
  cron: "30 9 * * *",
  weekdays: [0, 1, 2, 3, 4, 5, 6],
};
// qa2's paused schedule: 00:00 UTC on 1 January, and on no other day.
const NEW_YEAR: CheckinSendResponse = {
  kind: "dates",
  cron: "0 0 1 1 *",
  timezone: "UTC",
  local_time: "00:00:00",
  weekdays: null,
  month_days: [1],
  months: [1],
};
// Every hour from 09:00 to 17:00 on weekdays: no one time to say.
const HOURLY: CheckinSendResponse = {
  kind: "other",
  cron: "0 9-17 * * 1-5",
  timezone: "UTC",
  local_time: null,
  weekdays: null,
  ...NO_DATES,
};
// The scheduled send switched off (OPENPROGRAM_CHECKIN_FANOUT_ENABLED=false):
// the cron is what applies once it is on again, so it is never said as when.
const OFF: CheckinSendResponse = {
  kind: "off",
  cron: "30 9 * * 1-5",
  timezone: "UTC",
  local_time: null,
  weekdays: null,
  ...NO_DATES,
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
  assert.deepEqual(
    changeLines(initial, { ...initial, teamDays: true, timezone: null }, "UTC", SEND),
    ["Days: Mon–Fri → your team's (Mon–Fri)", "Time zone: Europe/Berlin → your team's (UTC)"],
  );
  assert.deepEqual(changeLines(initial, initial, "UTC", SEND), []);
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

test("a schedule with no one time says its cron, in UTC, and no days", () => {
  assert.equal(
    sendWords(HOURLY, "Europe/Berlin", FRIDAY_SUMMER),
    "on the schedule 0 9-17 * * 1-5 (UTC)",
  );
  assert.equal(nextSendAt(HOURLY, FRIDAY_SUMMER), null);
});

test("the time zone says what it changes, and that the send time isn't one of those things", () => {
  assert.equal(
    zoneEffectWords(SEND),
    "Decides which day your reply counts for. It doesn't change when the bot asks you: that is 09:30 UTC for everyone.",
  );
});

test("only the days the bot sends on are offered or shown as asked", () => {
  assert.deepEqual(sendDays(SEND), [0, 1, 2, 3, 4]);
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

// Liam, Omar and Hana on qa2: every day of their own, while the bot asks only on 1 Jan.
const EVERY = [0, 1, 2, 3, 4, 5, 6];

test("weekly: the send's time and days, and every day only when it sends on all seven", () => {
  assert.equal(
    askTimeWords(DAILY, "Europe/Berlin", FRIDAY_SUMMER),
    "The bot asks everyone at 09:30 UTC (11:30 in Europe/Berlin), every day: one time for the whole team, so it isn't yours to set. You choose which of those days it asks you, and your time zone.",
  );
  assert.equal(daysWords(EVERY, DAILY), "Every day");
  assert.deepEqual(sendDays(DAILY), EVERY);
  assert.equal(
    scheduleSummary(preference({ weekdays: EVERY, send: DAILY })),
    "Every day · Europe/Berlin",
  );
  assert.equal(
    adminSendWords(SEND, "Asia/Kolkata", FRIDAY_SUMMER),
    "The bot asks everyone at 09:30 UTC (15:00 in Asia/Kolkata), Mon–Fri: one send for the whole tenant. A member's days decide whether they are asked that day; their time zone decides which day a reply counts for, not when they are asked.",
  );
  assert.equal(daysLegendWords(SEND), "Days the bot asks you");
  assert.equal(
    daysHintWords(SEND, false),
    "On days you leave off, the bot doesn't ask you. These stay yours if the team's change.",
  );
});

test("dates: only on 1 Jan, on the clock of that day, and said not to be weekly", () => {
  assert.equal(
    askTimeWords(NEW_YEAR, "Europe/Berlin", FRIDAY_SUMMER),
    "Check-ins aren't on a weekly schedule right now. The bot asks everyone only on 1 Jan, at 00:00 UTC (01:00 in Europe/Berlin): one time for the whole team, so it isn't yours to set. You choose your days and your time zone.",
  );
  // The next send is Fri 1 Jan 2027, in winter: 01:00 in Berlin, not today's 02:00.
  assert.equal(nextSendAt(NEW_YEAR, FRIDAY_SUMMER)?.toISOString(), "2027-01-01T00:00:00.000Z");
  assert.equal(
    sendTimeWords(NEW_YEAR, "Asia/Kolkata", FRIDAY_SUMMER),
    "00:00 UTC (05:30 in Asia/Kolkata)",
  );
  assert.equal(
    sendTimeWords(NEW_YEAR, "Asia/Tokyo", FRIDAY_SUMMER),
    "00:00 UTC (09:00 in Asia/Tokyo)",
  );
  assert.equal(
    sendTimeWords(NEW_YEAR, "Pacific/Honolulu", FRIDAY_SUMMER),
    "00:00 UTC (14:00 the day before in Pacific/Honolulu)",
  );
  assert.equal(
    zoneEffectWords(NEW_YEAR),
    "Decides which day your reply counts for. It doesn't change when the bot asks you: that is 00:00 UTC on 1 Jan for everyone.",
  );
  assert.equal(
    adminSendWords(NEW_YEAR, "Europe/Berlin", FRIDAY_SUMMER),
    "Check-ins aren't on a weekly schedule right now. The bot asks everyone only on 1 Jan, at 00:00 UTC (01:00 in Europe/Berlin): one send for the whole tenant. A member's days only skip them when a send falls on a day they leave off; their time zone decides which day a reply counts for, not when they are asked.",
  );
});

test("dates are said as dates, days of the month or months", () => {
  const dates = (month_days: number[] | null, months: number[] | null) =>
    datesWords({ month_days, months });
  assert.equal(dates([1], [1]), "1 Jan");
  assert.equal(dates([1], [1, 4, 7, 10]), "1 Jan, 1 Apr, 1 Jul and 1 Oct");
  assert.equal(dates([15, 1], [7, 1]), "1 Jan, 15 Jan, 1 Jul and 15 Jul");
  assert.equal(dates([1, 2, 3], [1, 2, 3]), "the 1st, 2nd and 3rd of Jan, Feb and Mar");
  assert.equal(dates([1, 15], null), "the 1st and 15th of each month");
  assert.equal(
    dates([11, 12, 13, 21, 22, 23, 31], null),
    "the 11th, 12th, 13th, 21st, 22nd, 23rd and 31st of each month",
  );
  assert.equal(dates(null, [1, 7]), "each day of Jan and Jul");
  const twiceMonthly: CheckinSendResponse = {
    ...NEW_YEAR,
    cron: "0 9 1,15 * *",
    local_time: "09:00:00",
    month_days: [1, 15],
    months: null,
  };
  assert.equal(nextSendAt(twiceMonthly, FRIDAY_SUMMER)?.toISOString(), "2026-10-15T09:00:00.000Z");
  assert.match(
    askTimeWords(twiceMonthly, "UTC", FRIDAY_SUMMER),
    /The bot asks everyone only on the 1st and 15th of each month, at 09:00 UTC:/,
  );
});

test("other: a member reads that an admin set it, an admin reads the cron", () => {
  assert.equal(
    askTimeWords(HOURLY, "Europe/Berlin", FRIDAY_SUMMER),
    "The bot asks everyone on a schedule your admin set, the same for the whole team, so it isn't yours to set. You choose your days and your time zone.",
  );
  assert.equal(
    zoneEffectWords(HOURLY),
    "Decides which day your reply counts for. It doesn't change when the bot asks you.",
  );
  assert.equal(
    adminSendWords(HOURLY, "Europe/Berlin", FRIDAY_SUMMER),
    "The bot asks everyone on the cron schedule 0 9-17 * * 1-5, read in UTC: one schedule for the whole tenant. It isn't a simple weekly time, so the cron is the only account of when it asks. A member's days only skip them when a send falls on a day they leave off; their time zone decides which day a reply counts for, not when they are asked.",
  );
});

test("off a weekly schedule every day is offered, and a day only skips a send", () => {
  for (const send of [NEW_YEAR, HOURLY]) {
    assert.deepEqual(sendDays(send), EVERY);
    assert.deepEqual(askedDays([4, 5, 6], send), [4, 5, 6]);
    assert.equal(daysLegendWords(send), "Your days");
    assert.equal(
      daysHintWords(send, false),
      "Your days don't add asks: they only skip you when a send falls on a day you leave off. These stay yours if the team's change.",
    );
    assert.equal(
      daysHintWords(send, true),
      "Your days don't add asks: they only skip you when a send falls on a day you leave off. Following your team: when its days change, yours do too.",
    );
  }
  // Liam stored every day: the line says when the bot asks, and he is skipped on no day.
  const liam = preference({ weekdays: EVERY, send: NEW_YEAR });
  assert.equal(scheduleSummary(liam), "Only on 1 Jan · Europe/Berlin");
  assert.equal(
    scheduleSummary(preference({ weekdays: [0, 1, 2, 3, 4], send: NEW_YEAR })),
    "Only on 1 Jan · skips Sat, Sun · Europe/Berlin",
  );
  assert.equal(
    scheduleSummary(preference({ weekdays: EVERY, send: HOURLY, inherited: ["timezone"] })),
    "On your admin's schedule · team time zone (UTC)",
  );
  const initial = draftFrom(liam);
  assert.deepEqual(initial.weekdays, EVERY);
  assert.deepEqual(scheduleChanges(initial, { ...initial }), {});
  assert.deepEqual(scheduleChanges(initial, { ...initial, weekdays: toggleDay(EVERY, 6) }), {
    weekdays: [0, 1, 2, 3, 4, 5],
  });
  // Following the team shows the team's days (the dialog puts them in the draft).
  const team = { ...initial, teamDays: true, weekdays: [0, 1, 2, 3, 4] };
  assert.deepEqual(changeLines(initial, team, "UTC", NEW_YEAR), [
    "Days: Mon–Sun → your team's (Mon–Fri)",
  ]);
});

test("off: the screens say check-ins aren't sent on a schedule, and no time, cron or day", () => {
  assert.equal(
    askTimeWords(OFF, "Europe/Berlin", FRIDAY_SUMMER),
    "Check-ins aren't sent on a schedule right now. The bot won't ask you until your admin turns the scheduled send back on. You can still choose your days and your time zone: they apply from then.",
  );
  assert.equal(
    adminSendWords(OFF, "Europe/Berlin", FRIDAY_SUMMER),
    "Check-ins aren't sent on a schedule right now. The scheduled send is switched off (OPENPROGRAM_CHECKIN_FANOUT_ENABLED), so the bot asks nobody until it is on again. A member's days only matter then; their time zone decides which day a reply counts for.",
  );
  assert.equal(
    zoneEffectWords(OFF),
    "Decides which day your reply counts for. It doesn't change when the bot asks you.",
  );
  assert.equal(nextSendAt(OFF, FRIDAY_SUMMER), null);
  // Every day is offered and kept, so a member can set their days for when it is on.
  assert.deepEqual(sendDays(OFF), EVERY);
  assert.deepEqual(askedDays([4, 5, 6], OFF), [4, 5, 6]);
  assert.equal(daysLegendWords(OFF), "Your days");
  assert.equal(
    daysHintWords(OFF, false),
    "Your days only matter once check-ins are sent on a schedule again. These stay yours if the team's change.",
  );
  assert.equal(
    daysHintWords(OFF, true),
    "Your days only matter once check-ins are sent on a schedule again. Following your team: when its days change, yours do too.",
  );
  // The menu names no days: none of them is asked while it is off.
  assert.equal(
    scheduleSummary(preference({ weekdays: [0, 1, 2, 3, 4], send: OFF })),
    "Not sent on a schedule now · Europe/Berlin",
  );
  assert.equal(
    scheduleSummary(preference({ send: OFF, inherited: ["timezone"] })),
    "Not sent on a schedule now · team time zone (UTC)",
  );
  const draft = draftFrom(preference({ weekdays: EVERY, send: OFF }));
  assert.deepEqual(draft.weekdays, EVERY);
  assert.deepEqual(scheduleChanges(draft, { ...draft, weekdays: [0, 1, 2, 3, 4] }), {
    weekdays: [0, 1, 2, 3, 4],
  });
  for (const line of [
    askTimeWords(OFF, "Europe/Berlin", FRIDAY_SUMMER),
    adminSendWords(OFF, "Europe/Berlin", FRIDAY_SUMMER),
    zoneEffectWords(OFF),
    scheduleSummary(preference({ send: OFF })),
  ]) {
    assert.doesNotMatch(line, /09:30|30 9|Mon–Fri|\bat \d/, line);
  }
});

test("every day is said only of a send that is daily", () => {
  for (const send of [SEND, NEW_YEAR, HOURLY, OFF]) {
    const person = preference({ weekdays: EVERY, send });
    const draft = draftFrom(person);
    const words = [
      daysLabel(EVERY, send),
      daysWords(EVERY, send),
      scheduleSummary(person),
      askTimeWords(send, "Europe/Berlin", FRIDAY_SUMMER),
      adminSendWords(send, "Europe/Berlin", FRIDAY_SUMMER),
      zoneEffectWords(send),
      daysHintWords(send, false),
      ...changeLines(draft, { ...draft, teamDays: true }, "UTC", send),
    ];
    for (const line of words) assert.doesNotMatch(line, /every day/i, `${send.cron}: ${line}`);
  }
  assert.equal(daysLabel(EVERY, NEW_YEAR), "Mon–Sun");
  assert.equal(daysLabel(EVERY, DAILY), "every day");
});
