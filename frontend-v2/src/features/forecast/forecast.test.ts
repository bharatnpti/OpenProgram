import assert from "node:assert/strict";
import { test } from "node:test";

import type { ScopeDeliveryResponse } from "../../api/schema";
import { dayLabel, historyLabel, movedLabel, teamLabel } from "./forecast.ts";

const TODAY = new Date("2026-10-05T12:00:00");

const SCOPE: ScopeDeliveryResponse = {
  scope_kind: "project",
  scope_id: "checkout",
  project_id: "checkout",
  name: "Checkout",
  commitment: {
    target_date: "2026-11-23",
    original_date: "2026-11-14",
    times_moved: 2,
    moved_days: 9,
    changes: [],
  },
  target: "2026-11-23",
  target_source: "committed",
  jira_release_date: null,
  history: {
    p50: "2026-11-20",
    p85: "2026-11-26",
    remaining: 8,
    unit: "requirements",
    sample_days: 20,
    completed_in_sample: 31,
    reason: null,
    needed_days: 10,
  },
  team: { latest: "2026-11-23", latest_key: "CHK-104", dated: 5, undated: 3 },
  verdict: "at_risk",
  reasons: [],
  total: 40,
  open: 8,
};

test("a moved date says how often and how far", () => {
  assert.equal(movedLabel(SCOPE.commitment), "Moved twice, 9 days later than first set");
  assert.equal(movedLabel({ ...SCOPE.commitment, times_moved: 0, moved_days: 0 }), "");
});

test("forecast lines lead with the 85% date and admit when there is none", () => {
  assert.equal(historyLabel(SCOPE, TODAY), "85% by Thu 26 Nov · 50% by Fri 20 Nov");
  assert.equal(
    historyLabel(
      {
        ...SCOPE,
        history: { ...SCOPE.history, p50: null, p85: null, reason: "Too little history." },
      },
      TODAY,
    ),
    "Too little history.",
  );
  assert.equal(teamLabel(SCOPE, TODAY), "Mon 23 Nov (CHK-104) · 3 without a date");
});

test("a day in another year carries its year", () => {
  assert.equal(dayLabel("2027-01-04", TODAY), "Mon 4 Jan 2027");
  assert.equal(dayLabel(null, TODAY), "—");
});
