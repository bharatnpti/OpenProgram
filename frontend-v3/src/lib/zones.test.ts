import assert from "node:assert/strict";
import { test } from "node:test";

import { currentZoneName, deviceTimezone } from "./zones.ts";

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
