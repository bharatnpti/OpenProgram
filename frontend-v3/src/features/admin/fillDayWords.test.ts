import assert from "node:assert/strict";
import { test } from "node:test";

import { fillDayLine, fillDaySaved, fillDaySource } from "./fillDayWords.ts";

const say = {
  day: (iso: string) => `day(${iso.slice(0, 10)})`,
  name: (id: string) => (id === "U1001" ? "Asha Rao" : null),
};

test("the switch says what it does, and that the deployment's off wins", () => {
  assert.match(fillDayLine({ enabled: true, deployment_enabled: true }), /^On: .*never green\./);
  assert.match(fillDayLine({ enabled: false, deployment_enabled: true }), /^Off: /);
  assert.match(
    fillDayLine({ enabled: true, deployment_enabled: false }),
    /^Off for this deployment/,
  );
});

test("where the setting comes from names the admin; an id nobody names stays an id", () => {
  assert.equal(fillDaySource({ source: "default" }, say), "On by default.");
  assert.equal(
    fillDaySource(
      { source: "tenant", updated_by: "U1001", updated_at: "2026-10-06T09:00:00Z" },
      say,
    ),
    "Set by Asha Rao on day(2026-10-06).",
  );
  assert.equal(
    fillDaySource({ source: "tenant", updated_by: "auth0|abc", updated_at: null }, say),
    "Set by auth0|abc.",
  );
  assert.match(fillDaySaved(false), /keep no status/);
});
