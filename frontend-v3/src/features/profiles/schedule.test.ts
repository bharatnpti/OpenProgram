import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { PodCheckinsResponse } from "../../api/schema";
import { scheduleOf } from "./schedule.ts";

type Person = PodCheckinsResponse["developers"][number];

const person = (overrides: Partial<Person>): Person => ({
  developer_id: "U1",
  developer_name: "Ada Lind",
  state: "confirmed",
  source: "confirmed",
  status_as_of: null,
  summary: "",
  evidence: [],
  no_data_cause: null,
  asked_at: null,
  replied_at: null,
  reminders: [],
  checkin_time: null,
  timezone: null,
  ...overrides,
});

describe("each person's check-in time", () => {
  test("time and zone by member id, for a reader the server sends them to", () => {
    const schedule = scheduleOf({
      reads_schedule: true,
      developers: [
        person({ developer_id: "U1", checkin_time: "09:30", timezone: "Europe/Berlin" }),
        person({ developer_id: "U2", checkin_time: "10:00" }),
        person({ developer_id: "U3" }),
      ],
    });
    assert.equal(schedule?.get("U1"), "Check-in 09:30 · Europe/Berlin");
    assert.equal(schedule?.get("U2"), "Check-in 10:00");
    assert.equal(schedule?.has("U3"), false);
  });

  test("nothing for a reader the server does not send them to", () => {
    assert.equal(
      scheduleOf({
        reads_schedule: false,
        developers: [person({ checkin_time: "09:30", timezone: "Europe/Berlin" })],
      }),
      null,
    );
    assert.equal(scheduleOf(undefined), null);
  });
});
