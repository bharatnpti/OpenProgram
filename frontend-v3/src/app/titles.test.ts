import assert from "node:assert/strict";
import { test } from "node:test";

import { screenTitle } from "./titles.ts";

const NAMES: Record<string, string> = {
  "pod:pod-payments": "Payments Pod",
  "project:project-checkout": "Checkout Revamp",
};
const names = (kind: string, id: string) => NAMES[`${kind}:${id}`];

test("each screen has its own tab title, not the app's one name", () => {
  assert.equal(screenTitle("/today"), "Today · OpenProgram");
  assert.equal(screenTitle("/delivery"), "Delivery · OpenProgram");
  assert.equal(screenTitle("/signals"), "Signals · OpenProgram");
  assert.equal(screenTitle("/coordination"), "Coordination · OpenProgram");
  assert.equal(screenTitle("/reports"), "Reports · OpenProgram");
  assert.equal(screenTitle("/chat"), "Chat · OpenProgram");
  assert.equal(screenTitle("/admin"), "Admin · OpenProgram");
});

test("a node or a project's report names what it shows", () => {
  assert.equal(
    screenTitle("/delivery/pod/pod-payments", names),
    "Payments Pod · Delivery · OpenProgram",
  );
  assert.equal(
    screenTitle("/reports/project-checkout/overall", names),
    "Overall report · Checkout Revamp · Reports · OpenProgram",
  );
  assert.equal(
    screenTitle("/reports/project-checkout/daily", names),
    "Daily report · Checkout Revamp · Reports · OpenProgram",
  );
});

test("a node nobody has named yet leaves its name out, never shows an id", () => {
  assert.equal(screenTitle("/delivery/pod/pod-unknown", names), "Delivery · OpenProgram");
  assert.equal(
    screenTitle("/reports/project-unknown/overall", names),
    "Overall report · Reports · OpenProgram",
  );
});

test("an address no screen owns falls back to the app's name", () => {
  assert.equal(screenTitle("/"), "OpenProgram");
  assert.equal(screenTitle("/nowhere"), "OpenProgram");
});
