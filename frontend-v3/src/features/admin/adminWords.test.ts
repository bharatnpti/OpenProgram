import assert from "node:assert/strict";
import { test } from "node:test";

import { minutesLabel, slugId } from "./adminWords.ts";

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
