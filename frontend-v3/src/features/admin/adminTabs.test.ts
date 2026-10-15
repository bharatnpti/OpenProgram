import assert from "node:assert/strict";
import { test } from "node:test";

import { ADMIN_GROUPS, ADMIN_TABS, adminTab } from "./adminTabs.ts";

test("Admin keeps its fifteen tabs and their keys, each in one of five groups", () => {
  assert.deepEqual(
    ADMIN_GROUPS.map((group) => group.label),
    ["Check-ins", "Delivery rules", "Connections", "Organisation", "Look"],
  );
  const keys = ADMIN_TABS.map((tab) => tab.key);
  assert.equal(keys.length, 15);
  assert.equal(new Set(keys).size, 15, "no tab twice");
  for (const key of [
    "checkins",
    "sources",
    "integrations",
    "jira-writes",
    "stages",
    "gates",
    "readiness",
    "hygiene",
    "escalation",
    "contacts",
    "branding",
    "entities",
    "links",
    "directory",
    "profiles",
  ]) {
    assert.ok(keys.includes(key as (typeof keys)[number]), key);
  }
});

test("a link's tab opens, an unknown one opens Check-ins", () => {
  assert.equal(adminTab("escalation"), "escalation");
  assert.equal(adminTab("jira-writes"), "jira-writes");
  assert.equal(adminTab("nope"), "checkins");
  assert.equal(adminTab(null), "checkins");
});
