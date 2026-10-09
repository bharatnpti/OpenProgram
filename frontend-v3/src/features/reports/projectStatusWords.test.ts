import assert from "node:assert/strict";
import { test } from "node:test";

import type { ScopeDeliveryResponse } from "../../api/schema";
import { projectStatus } from "./projectStatusWords.ts";

const scope = (over: Partial<ScopeDeliveryResponse> = {}): ScopeDeliveryResponse => ({
  scope_kind: "project",
  scope_id: "project-checkout",
  project_id: "project-checkout",
  name: "Checkout Revamp",
  commitment: {
    target_date: "2026-10-30",
    original_date: "2026-10-30",
    times_moved: 0,
    moved_days: null,
    changes: [],
  },
  target: "2026-10-30",
  target_source: "committed",
  jira_release_date: null,
  history: {
    p50: "2026-11-04",
    p85: "2026-11-12",
    remaining: 17,
    unit: "requirements",
    sample_days: 21,
    completed_in_sample: 6,
    reason: null,
  },
  team: { latest: "2026-11-03", latest_key: "CHK-103", dated: 14, undated: 3 },
  verdict: "at_risk",
  reasons: [],
  total: 24,
  open: 17,
  ...over,
});

test("a card that shows the delivery says the verdict's words, whatever the project colour says", () => {
  // The project's own colour is amber, the delivery says off track: only the verdict is said.
  assert.deepEqual(
    projectStatus({ scope: scope({ verdict: "off_track" }), reading: false }, "amber"),
    {
      label: "Off track",
      tone: "danger",
    },
  );
  assert.deepEqual(
    projectStatus({ scope: scope({ verdict: "on_track" }), reading: false }, "red"),
    {
      label: "On track",
      tone: "success",
    },
  );
});

test("a verdict the date line already says is not said twice", () => {
  const noDate = scope({ verdict: "no_date", target: null });
  assert.equal(projectStatus({ scope: noDate, reading: false }, "amber"), null);
});

test("while the delivery is read the card shows no colour that may then contradict it", () => {
  assert.equal(projectStatus({ scope: undefined, reading: true }, "amber"), null);
});

test("a card with no delivery to show says the project's colour in words, never the enum", () => {
  assert.deepEqual(projectStatus(null, "amber"), { label: "At risk", tone: "warning" });
  assert.deepEqual(projectStatus(null, "green"), { label: "On track", tone: "success" });
  assert.deepEqual(projectStatus(null, "red"), { label: "Off track", tone: "danger" });
  assert.deepEqual(projectStatus(null, "unknown"), { label: "Status unknown", tone: "neutral" });
  assert.deepEqual(projectStatus(null, null), { label: "Status unknown", tone: "neutral" });
  // The delivery could not be read: the project's colour stands in, in words.
  assert.deepEqual(projectStatus({ scope: undefined, reading: false }, "amber"), {
    label: "At risk",
    tone: "warning",
  });
});
