import assert from "node:assert/strict";
import { test } from "node:test";

import { currentZoneName, deviceTimezone, formatInZone, reportZone } from "./zones.ts";

test("a zone a browser reports under its old name is sent under its current one", () => {
  assert.equal(currentZoneName("Asia/Calcutta"), "Asia/Kolkata");
  assert.equal(currentZoneName("Europe/Kiev"), "Europe/Kyiv");
  assert.equal(currentZoneName("Asia/Saigon"), "Asia/Ho_Chi_Minh");
  assert.equal(currentZoneName("America/Buenos_Aires"), "America/Argentina/Buenos_Aires");
});

test("every other zone is left as it is", () => {
  assert.equal(currentZoneName("Asia/Kolkata"), "Asia/Kolkata");
  assert.equal(currentZoneName("Europe/Berlin"), "Europe/Berlin");
  assert.equal(currentZoneName("UTC"), "UTC");
});

test("the device's zone is always one the server knows", () => {
  const zone = deviceTimezone();
  assert.ok(zone === null || typeof zone === "string");
  assert.notEqual(zone, "Asia/Calcutta");
  assert.notEqual(zone, "Europe/Kiev");
});

test("a new day report starts in the team's zone, not the browser's", () => {
  assert.deepEqual(reportZone("Europe/Berlin", "Asia/Kolkata"), {
    zone: "Europe/Berlin",
    from: "team",
  });
  assert.deepEqual(reportZone(" Europe/Berlin ", null), { zone: "Europe/Berlin", from: "team" });
});

test("without the team's zone it is the device's, and then UTC", () => {
  assert.deepEqual(reportZone(null, "Asia/Kolkata"), { zone: "Asia/Kolkata", from: "device" });
  assert.deepEqual(reportZone("", "Asia/Kolkata"), { zone: "Asia/Kolkata", from: "device" });
  assert.deepEqual(reportZone(undefined, null), { zone: "UTC", from: "none" });
});

test("a send is read in the schedule's zone, with the zone named as the schedule names it", () => {
  // 19:00 UTC is 21:00 in Berlin in October (summer time), 20:00 in January.
  assert.equal(
    formatInZone("2026-10-05T19:00:00Z", "Europe/Berlin"),
    "Mon 5 Oct 21:00 Europe/Berlin",
  );
  assert.equal(
    formatInZone("2026-01-12T19:00:00Z", "Europe/Berlin"),
    "Mon 12 Jan 20:00 Europe/Berlin",
  );
  assert.equal(
    formatInZone("2026-10-05T15:30:05+00:00", "Europe/Berlin"),
    "Mon 5 Oct 17:30 Europe/Berlin",
  );
});

test("the zone moves the day as well as the hour, and midnight is 00:00", () => {
  // 22:30 UTC on Mon 5 Oct is already Tue 6 Oct in Berlin and in Tokyo, still Mon in New York.
  assert.equal(
    formatInZone("2026-10-05T22:30:00Z", "Europe/Berlin"),
    "Tue 6 Oct 00:30 Europe/Berlin",
  );
  assert.equal(formatInZone("2026-10-05T22:30:00Z", "Asia/Tokyo"), "Tue 6 Oct 07:30 Asia/Tokyo");
  assert.equal(
    formatInZone("2026-10-05T22:30:00Z", "America/New_York"),
    "Mon 5 Oct 18:30 America/New_York",
  );
  assert.equal(formatInZone("2026-10-05T22:00:00Z", "UTC"), "Mon 5 Oct 22:00 UTC");
});

test("nothing to read, or a zone this browser does not know, is said plainly", () => {
  assert.equal(formatInZone(null, "Europe/Berlin"), "—");
  assert.equal(formatInZone(undefined, "Europe/Berlin"), "—");
  assert.equal(formatInZone("not a time", "Europe/Berlin"), "—");
  assert.equal(formatInZone("2026-10-05T19:00:00Z", "Mars/Olympus"), "Mon 5 Oct 19:00 UTC");
});
